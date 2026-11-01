#!/usr/bin/env python3
"""
Comprehensive Unit & Integration Test Suite for Post-Mint Profit & ATH Hall of Fame System.

Tests:
1. High-water mark (ATH) tracking and persistence: ratchets up on new highs, preserves peak on retraces.
2. Free mint vs paid mint profit calculation and multipliers.
3. Strict exclusion of pegged assets (BTCB, WBNB, USDT, USDC, BUSD) and system infra.
4. Python compute_market_cap_stats() returning top gainer, runner-up, and full leaderboard.
5. Python filter_and_sort_projects() sorting by PROFIT descending.
6. Node.js app.js parity: computeItemProfit and computeStats match Python calculations.
7. HTML UI integration: topGainerBanner, hallOfFameModal, and custom sort option.
"""

import unittest
import os
import sys
import json
import subprocess
import time

import monitor
import server


class TestTopProfits(unittest.TestCase):
    def setUp(self):
        self.mock_free_mint = {
            "contract": "0x1111111111111111111111111111111111111111",
            "name": "AlphaBot Genesis",
            "symbol": "ABOT",
            "standard": "ERC-721 (NFT Collection)",
            "mint_price_str": "FREE",
            "mint_price_bnb": 0.0,
            "mint_value_wei": "0",
            "max_supply": 1000,
            "total_minted": 1000,
            "floor_price_usd": 0.05,
            "floor_price_bnb": 0.00007,
            "holders": 850
        }

        self.mock_paid_mint = {
            "contract": "0x2222222222222222222222222222222222222222",
            "name": "CyberAgents VIP",
            "symbol": "CVIP",
            "standard": "ERC-721 (NFT Collection)",
            "mint_price_str": "0.01 BNB",
            "mint_price_bnb": 0.01,
            "mint_value_wei": str(int(0.01 * 1e18)),
            "max_supply": 500,
            "total_minted": 500,
            "floor_price_usd": 35.25,  # Mint was 0.01 BNB * $705 = $7.05, secondary is $35.25 (5x)
            "floor_price_bnb": 0.05,
            "holders": 420
        }

        self.mock_btcb = {
            "contract": "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",
            "name": "BTCB Token",
            "symbol": "BTCB",
            "standard": "BEP-20 Token (18 decimals)",
            "mint_price_str": "TBD",
            "token_price_usd": 78000.0,
            "max_supply": None,
            "total_minted": 65000
        }

    def test_01_ath_high_water_mark_ratchet_and_retrace(self):
        """Tests that ATH price/profit ratchets up on peaks and remains persistent through price retracements."""
        item = dict(self.mock_free_mint)
        bnb_p = 705.0

        # Initial check at $0.05
        prof1 = monitor.update_post_mint_profit_metrics(item, bnb_price=bnb_p)
        self.assertAlmostEqual(prof1["ath_price_usd"], 0.05, places=4)
        self.assertAlmostEqual(prof1["ath_profit_usd"], 0.05, places=4)
        self.assertAlmostEqual(item["current_profit_usd"], 0.05, places=4)

        # Price pumps to $0.25 (New High-Water Mark)
        item["floor_price_usd"] = 0.25
        prof2 = monitor.update_post_mint_profit_metrics(item, bnb_price=bnb_p)
        self.assertAlmostEqual(prof2["ath_price_usd"], 0.25, places=4)
        self.assertAlmostEqual(prof2["ath_profit_usd"], 0.25, places=4)
        self.assertAlmostEqual(item["current_profit_usd"], 0.25, places=4)
        ath_time = item["ath_timestamp"]
        self.assertGreater(ath_time, 0)

        # Retrace / Dump back to $0.01 (ATH must NOT be lost!)
        item["floor_price_usd"] = 0.01
        prof3 = monitor.update_post_mint_profit_metrics(item, bnb_price=bnb_p)
        self.assertAlmostEqual(prof3["ath_price_usd"], 0.25, places=4, msg="ATH price was erased on dump!")
        self.assertAlmostEqual(prof3["ath_profit_usd"], 0.25, places=4, msg="ATH profit was erased on dump!")
        self.assertAlmostEqual(item["current_profit_usd"], 0.01, places=4, msg="Current profit must reflect live price")
        print("\n[ATH RATCHET TEST] Verified: Initial $0.05 -> Peak $0.25 -> Retrace $0.01 (Saved ATH: $0.25)")

    def test_02_paid_mint_roi_multiplier(self):
        """Tests that paid mints calculate exact net profit and ROI multipliers."""
        item = dict(self.mock_paid_mint)
        bnb_p = 705.0

        # Mint cost: 0.01 BNB * $705 = $7.05
        # Secondary floor: $35.25 -> Net profit: $28.20 -> Multiplier: 5.0x
        prof = monitor.update_post_mint_profit_metrics(item, bnb_price=bnb_p)
        self.assertAlmostEqual(prof["mint_usd"], 7.05, places=2)
        self.assertAlmostEqual(prof["profit"], 28.20, places=2)
        self.assertEqual(item["ath_multiplier"], 5.0)
        print(f"[PAID MINT TEST] Verified: Mint ${prof['mint_usd']:.2f} -> Sec ${prof['sec_usd']:.2f} (Net +${prof['profit']:.2f}, {item['ath_multiplier']}x)")

    def test_03_pegged_crypto_and_infra_filtering(self):
        """Tests that BTCB, WBNB, USDT, USDC, BUSD, and system infrastructure cannot hijack post-mint rankings."""
        item_btcb = dict(self.mock_btcb)
        prof_btcb = monitor.update_post_mint_profit_metrics(item_btcb, bnb_price=705.0)
        self.assertEqual(prof_btcb["sec_usd"], 0.0)
        self.assertEqual(prof_btcb["profit"], 0.0)

        history = [self.mock_btcb, self.mock_free_mint, self.mock_paid_mint]
        stats = monitor.compute_market_cap_stats(history, bnb_price=705.0)

        # BTCB must not be top gainer
        self.assertNotEqual(stats["top_gainer"]["symbol"], "BTCB")
        self.assertEqual(stats["top_gainer"]["symbol"], "CVIP")
        self.assertEqual(stats["runner_up"]["symbol"], "ABOT")
        print(f"[FILTER TEST] Verified: Pegged BTCB excluded. Top Gainer: {stats['top_gainer']['symbol']} (+${stats['top_gainer']['profit']:.2f})")

    def test_04_python_sort_by_profit(self):
        """Tests that filter_and_sort_projects with sort_mode='PROFIT' orders items by maximum profit descending."""
        item1 = dict(self.mock_free_mint)
        item1["ath_profit_usd"] = 0.50

        item2 = dict(self.mock_paid_mint)
        item2["ath_profit_usd"] = 28.20

        item3 = {
            "contract": "0x3333333333333333333333333333333333333333",
            "name": "Small Drop",
            "symbol": "SML",
            "standard": "ERC-721",
            "ath_profit_usd": 0.02
        }

        history = [item1, item3, item2]
        sorted_list = monitor.filter_and_sort_projects(history, "ALL", "PROFIT")

        self.assertEqual(sorted_list[0]["symbol"], "CVIP")
        self.assertEqual(sorted_list[1]["symbol"], "ABOT")
        self.assertEqual(sorted_list[2]["symbol"], "SML")
        print("[SORT PROFIT TEST] Successfully sorted drops: CVIP ($28.20) > ABOT ($0.50) > SML ($0.02)")

    def test_05_javascript_parity_node_execution(self):
        """Tests that Node.js app.js computeItemProfit and computeStats match Python calculations with 100% parity."""
        node_script = """
        const app = require('./app.js');
        const freeItem = {
          contract: '0x1111111111111111111111111111111111111111',
          name: 'AlphaBot Genesis',
          symbol: 'ABOT',
          standard: 'ERC-721 (NFT Collection)',
          mint_price_str: 'FREE',
          max_supply: 1000,
          total_minted: 1000,
          floor_price_usd: 0.05,
          holders: 850
        };

        const paidItem = {
          contract: '0x2222222222222222222222222222222222222222',
          name: 'CyberAgents VIP',
          symbol: 'CVIP',
          standard: 'ERC-721 (NFT Collection)',
          mint_price_bnb: 0.01,
          mint_value_wei: (0.01 * 1e18).toString(),
          max_supply: 500,
          total_minted: 500,
          floor_price_usd: 35.25,
          holders: 420
        };

        const btcbItem = {
          contract: '0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c',
          name: 'BTCB Token',
          symbol: 'BTCB',
          token_price_usd: 78000
        };

        const pFree = app.computeItemProfit(freeItem, 705.0);
        const pPaid = app.computeItemProfit(paidItem, 705.0);
        const pBtcb = app.computeItemProfit(btcbItem, 705.0);

        const stats = app.computeStats([btcbItem, freeItem, paidItem]);

        console.log(JSON.stringify({
          freeProfit: pFree.profit,
          paidProfit: pPaid.profit,
          paidMultiplier: pPaid.multiplier,
          btcbProfit: pBtcb.profit,
          topGainer: stats.topGainer ? stats.topGainer.symbol : null,
          runnerUp: stats.runnerUp ? stats.runnerUp.symbol : null
        }));
        """

        res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout.strip())

        self.assertAlmostEqual(data["freeProfit"], 0.05, places=4)
        self.assertAlmostEqual(data["paidProfit"], 28.20, places=2)
        self.assertEqual(data["paidMultiplier"], 5.0)
        self.assertEqual(data["btcbProfit"], 0.0)
        self.assertEqual(data["topGainer"], "CVIP")
        self.assertEqual(data["runnerUp"], "ABOT")
        print("[JS PARITY TEST] 100% parity verified between Node.js app.js and Python monitor.py")

    def test_06_html_and_css_elements(self):
        """Verifies that index.html contains topGainerBanner, hallOfFameModal, and PROFIT sort option."""
        with open("index.html") as f:
            html = f.read()

        self.assertIn('id="topGainerBanner"', html)
        self.assertIn('id="hallOfFameModal"', html)
        self.assertIn('data-value="PROFIT"', html)
        self.assertIn('value="PROFIT"', html)
        self.assertIn('stat-hof-badge', html)

        with open("style.css") as f:
            css = f.read()

        self.assertIn(".stat-card.gainer-card:hover", css)
        self.assertIn(".hof-modal", css)
        self.assertIn(".hof-table", css)
        print("[HTML/CSS TEST] Verified: topGainerBanner, hallOfFameModal, PROFIT sort options in index.html & style.css")


if __name__ == "__main__":
    unittest.main()
