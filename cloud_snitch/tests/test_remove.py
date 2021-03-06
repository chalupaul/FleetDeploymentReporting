import mock
import sys
import unittest

from io import StringIO

from cloud_snitch.remove import delete_until_zero
from cloud_snitch.remove import parser
from cloud_snitch.remove import prune


class TestArgParser(unittest.TestCase):
    """Test the argument parser."""

    def setUp(self):
        self.old_stream = sys.stderr
        self.stream = StringIO()
        sys.stderr = self.stream

    def tearDown(self):
        sys.stderr = self.old_stream
        self.stream.close()

    def test_missing_positionals(self):
        """Test with 0 args."""
        with self.assertRaises(SystemExit):
            parser.parse_args([])

    def test_defaults(self):
        """Test that skip defaults to false."""
        args = parser.parse_args(['some-uuid'])
        self.assertFalse(args.skip)

    def test_dash_s(self):
        """Test skip with -s"""
        args = parser.parse_args(['some-uuid', '-s'])
        self.assertTrue(args.skip)

    def test_dash_dash_yes(self):
        """Test skip with --skip"""
        args = parser.parse_args(['some-uuid', '--skip'])
        self.assertTrue(args.skip)


class LoopStopperError(Exception):
    pass


class FakeResult(dict):
    def __init__(self, deleted):
        self['deleted'] = deleted

    def single(self):
        return self


class FakeTransaction:

    def __init__(self, data, error_after=2):
        self.data = data
        self.error_after = error_after
        self.calls = 0
        self.query = None
        self.params = None

    def begin_transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def run(self, query, **params):
        self.calls += 1
        if self.calls > self.error_after:
            raise LoopStopperError("Stopping a loop")
        self.query = query
        self.params = params
        return self.data[self.calls - 1]


class TestDeleteUntilZero(unittest.TestCase):
    """Test delete_until_zero function."""

    def test_cipher_limit(self):
        """Test that limit is injected into query."""
        fake_tx = FakeTransaction([FakeResult(1)], error_after=1)
        session = mock.Mock()
        session.begin_transaction = mock.Mock()
        session.begin_transaction.return_value = fake_tx

        with self.assertRaises(LoopStopperError):
            delete_until_zero(session, 'test', params=None, limit=5)

        self.assertEqual(
            fake_tx.query,
            'test WITH n LIMIT 5 DETACH DELETE n RETURN count(*) as `deleted`'
        )
        self.assertFalse(fake_tx.params)

    def test_loop_termination(self):
        """Test that loop terminates when deleted count from query is 0."""
        fake_results = [
            FakeResult(10),
            FakeResult(5),
            FakeResult(1),
            FakeResult(0)
        ]
        fake_tx = FakeTransaction(fake_results, error_after=10)
        session = mock.Mock()
        session.begin_transaction = mock.Mock()
        session.begin_transaction.return_value = fake_tx

        deleted = delete_until_zero(session, 'test', params=None, limit=5)
        self.assertEqual(deleted, 16)


class FakeEnvironment:
    def __init__(self, account_number='12345', name='testenv'):
        self.account_number = account_number
        self.name = name
        self.account_number_name = (
            '{}-{}'.format(self.account_number, self.name)
        )


class TestPrune(unittest.TestCase):
    """Test the prune function."""
    @mock.patch('cloud_snitch.remove.delete_until_zero')
    @mock.patch('cloud_snitch.remove.registry')
    def test_shared(self, m_registry, m_delete):
        """Test that no action is taken on a shared leaf node."""
        m_registry.is_shared.return_value = True
        m_delete.return_value = 0
        path = ['Environment', 'Host', 'AptPackage']
        stats = {}
        prune('session', FakeEnvironment(), path, stats)
        m_delete.assert_not_called()
        self.assertTrue(stats is not None)

    @mock.patch('cloud_snitch.remove.delete_until_zero')
    @mock.patch('cloud_snitch.remove.registry')
    def test_non_shared_leaf(self, m_registry, m_delete):
        """Test pruning a shared non leaf."""
        m_registry.is_shared.return_value = False
        m_delete.side_effect = [1, 2]
        path = ['Environment', 'Host', 'AptPackage']
        stats = {}
        stats = prune('session', FakeEnvironment(), path, stats)
        self.assertEqual(m_delete.call_count, 2)
        self.assertEqual(stats['AptPackage'], 2)
        self.assertEqual(stats['AptPackageState'], 1)
