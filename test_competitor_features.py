import pytest
from unittest.mock import MagicMock
import monitor

def test_dev_funding_root_and_cluster():
    rpc = MagicMock()
    rpc.get_balance.return_value = int(1.5 * 1e18)
    rpc.get_tx_count.return_value = 10
    rpc.call_contract.return_value = None

    contract_addr = "0x1111111111111111111111111111111111111111"
    deployer_addr = "0x2222222222222222222222222222222222222222"
    root_addr = "0x9999999999999999999999999999999999999999"

    # History contains other contracts launched by same root or different deployers in same cluster
    history = [
        {
            "contract": "0xaaaa111111111111111111111111111111111111",
            "deployer": "0x3333333333333333333333333333333333333333",
            "dev_root": root_addr
        },
        {
            "contract": "0xbbbb222222222222222222222222222222222222",
            "deployer": "0x4444444444444444444444444444444444444444",
            "dev_root": root_addr
        }
    ]

    details = {
        "dev_root": root_addr,
        "name": "Test Cluster Token",
        "symbol": "TCT",
        "classification": {"category": "TOKEN", "standard": "BEP-20"}
    }

    stats = monitor.analyze_market_interest(rpc, contract_addr, deployer_addr, details, history=history)

    assert stats["dev_root"] == root_addr.lower()
    assert stats["cluster_devs"] >= 3  # deployer_addr + 2 history deployers
    assert stats["cluster_n"] >= 3     # 2 history drops + current drop
    assert any("Dev Cluster" in flag for flag in stats["risk_flags"])
    assert "hold" in stats
    assert "public" in stats["hold"]

def test_holder_distribution_breakdown():
    rpc = MagicMock()
    rpc.get_balance.return_value = int(1.0 * 1e18)
    rpc.get_tx_count.return_value = 5
    rpc.call_contract.return_value = None

    contract_addr = "0x1234567890123456789012345678901234567890"
    deployer_addr = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"

    # Details with high dev holding (18.5%)
    details = {
        "hold": {
            "dev": 18.5,
            "insider": 5.0,
            "smart": 12.0,
            "sniper": 4.5,
            "fresh": 2.0,
            "public": 58.0
        },
        "name": "Dev Heavy Token",
        "symbol": "DHT",
        "classification": {"category": "TOKEN", "standard": "BEP-20"}
    }

    stats = monitor.analyze_market_interest(rpc, contract_addr, deployer_addr, details, history=[])

    assert stats["hold"]["dev"] == 18.5
    assert stats["hold"]["smart"] == 12.0
    assert stats["hold"]["public"] == 58.0
    assert any("High Dev Holding" in flag for flag in stats["risk_flags"])
