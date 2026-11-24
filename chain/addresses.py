"""The contract addresses the monitor knows by name: the Agent ID registry, the Four.meme and vault launchpads, PancakeSwap, the
quote tokens, and the set of infrastructure addresses whose transactions are never drop candidates. Moved out of monitor.py
unchanged; monitor.py re-exports every name, so `monitor.WBNB_ADDRESS` and friends keep working."""

import os

# Official Binance .ai Agent ID Registry on BSC
AGENT_ID_REGISTRY = "0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F"

AGENT_ID_BYTES = "60b9262456de011f7d15a54cdd8c48baaa8b022f"

# Space ID Master Registry (Name Service Layer on BSC)
SPACE_ID_MASTER_REGISTRY = "0x08CEd32a7f3eeC915Ba84415e9C07a7286977956"

SPACE_ID_MASTER_BYTES = "08ced32a7f3eec915ba84415e9c07a7286977956"

# Four.meme Agent ID Launchpad & Factory
FOUR_MEME_FACTORY = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"

FOUR_MEME_FACTORY_BYTES = "5c952063c7fc8610ffdb798152d69f0b9550762b"

FOUR_MEME_ROUTER = "0xF251F83e40a78868FcfA3FA4599Dad6494E46034"

FOUR_MEME_ROUTER_BYTES = "f251f83e40a78868fcfa3fa4599dad6494e46034"

# Set of all tracked target registry & launchpad address byte-strings (lowercase hex without 0x)
TRACKED_REGISTRY_BYTES = [
    AGENT_ID_BYTES,
    SPACE_ID_MASTER_BYTES,
    FOUR_MEME_FACTORY_BYTES,
    FOUR_MEME_ROUTER_BYTES,
]

DEFAULT_WALLET = os.environ.get("AGENT_WALLET", "0xA3FbE3664dB8478c094dD36b2BB306d72CBCb37A")

# On-Chain DEX & Infrastructure Addresses
PANCAKE_FACTORY_V2 = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"

PANCAKE_ROUTER_V2 = "0x10ED43C718714eb63d5aA57B78B54704E256024E"

WBNB_ADDRESS = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"

USDT_ADDRESS = "0x55d398326f99059fF775485246999027B3197955"

USD1_ADDRESS = "0x8d0D000ee44948FC98c9B98A4FA4921476f08B0D"

BNCB_ADDRESS = "0x4902C5ebc598265ED2212b559b042de8a5eeec3f"

USDC_ADDRESS = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"

# Recognized Launchpad & Vault Factories
VAULT_FACTORIES = [
    "0x90497450f2a706f1951b5bdda52b4e5d16f34c06",  # Flap Vault Factory (AIDROP, etc.)
    FOUR_MEME_FACTORY.lower(),                       # Four.meme Token Factory / AMAP
    FOUR_MEME_ROUTER.lower()                         # Four.meme Buy Router
]


def known_infra_addresses() -> set:
    """Routers, factories and registries whose transactions are never treated as drop candidates."""
    return {
        PANCAKE_FACTORY_V2.lower(),
        WBNB_ADDRESS.lower(),
        USDT_ADDRESS.lower(),
        AGENT_ID_REGISTRY.lower(),
        SPACE_ID_MASTER_REGISTRY.lower(),
        FOUR_MEME_FACTORY.lower(),
        FOUR_MEME_ROUTER.lower(),
        PANCAKE_ROUTER_V2.lower(),
        "0x13f4ea83d0bd40e75c8222255bc855a974568dd4",
    }
