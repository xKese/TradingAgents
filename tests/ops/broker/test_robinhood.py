from ops.broker.mcp_client import RobinhoodMCPClient
from tests.ops.broker.fakes import FakeMCPClient


def test_fake_client_satisfies_protocol():
    client: RobinhoodMCPClient = FakeMCPClient()
    assert isinstance(client, RobinhoodMCPClient)
