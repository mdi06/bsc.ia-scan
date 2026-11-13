#!/usr/bin/env python3
"""
Unit and Integration Test Suite for Option D:
Element Market NFT Volume Spike Detector (nftVolWatch)
Validates:
1. Element ERC721SellOrderFilled log decoding (parse_element_trade_log)
2. NFT Volume Surge conviction engine (evaluate_nft_surge)
3. Cross-language parity between Python and JavaScript (Node.js)
4. Portfolio Take-Profit / Exit signal for surging NFTs
5. Filter & Query integration with BSC RPC
6. Gated safety compliance: NO auto-trades, manual listing / review only
"""

import os
import sys
import json
import subprocess
import unittest

# Ensure project root is in path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import monitor

class TestOptionD(unittest.TestCase):
    def setUp(self):
        self.rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)
        self.sample_col = "0x3048e07c057ef694d4e431154dafad473d1aa18b"  # Lazabit
        self.sample_nomio = "0x48b5e5573e76caaa41a893496de6c857720e6b40"  # Nomio

    def test_01_element_log_decoding(self):
        """Test parse_element_trade_log correctly parses 32-byte ABI encoded data."""
        # Construct synthetic ERC721SellOrderFilled event log
        order_hash = "11" * 32
        seller = "000000000000000000000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        buyer = "000000000000000000000000bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        fee = f"{int(0.0001 * 1e18):064x}"
        # Native BNB payment token (0xeeee...eeee)
        pay_token = "000000000000000000000000eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        # Price: 0.05 BNB in wei = 50000000000000000
        price_wei = int(0.05 * 1e18)
        price_hex = f"{price_wei:064x}"
        token_id = f"{288:064x}"
        col_addr = f"000000000000000000000000{self.sample_col[2:].lower()}"

        raw_data = "0x" + order_hash + seller + buyer + fee + pay_token + price_hex + token_id + col_addr
        fake_log = {
            "address": monitor.ELEMENT_EX,
            "topics": [monitor.T_721_FILLED],
            "data": raw_data,
            "transactionHash": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            "blockNumber": hex(131030000)
        }

        parsed = monitor.parse_element_trade_log(fake_log)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["collection"], self.sample_col.lower())
        self.assertEqual(parsed["token_id"], 288)
        self.assertTrue(parsed["is_bnb"])
        self.assertAlmostEqual(parsed["price_bnb"], 0.05, places=6)
        self.assertEqual(parsed["seller"], "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(parsed["buyer"], "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        self.assertEqual(parsed["block"], 131030000)

        # Test corrupt or truncated log
        corrupt_log = {"address": monitor.ELEMENT_EX, "data": "0x1234"}
        self.assertIsNone(monitor.parse_element_trade_log(corrupt_log))

    def test_02_nft_surge_conviction_thresholds(self):
        """Test evaluate_nft_surge threshold logic for held vs unheld items."""
        nft_item = {
            "contract": self.sample_col,
            "name": "Lazabit",
            "standard": "ERC-721 (NFT Collection)",
            "is_nft": True
        }

        # 1. Non-NFT asset should NEVER trigger surge
        token_item = {"contract": "0x123", "name": "Token", "standard": "BEP-20 Token (18 decimals)"}
        res_tok = monitor.evaluate_nft_surge(token_item, [0.1, 0.2, 0.3], user_held=5)
        self.assertFalse(res_tok["is_nft_surge"])

        # 2. Wallet-held item with 3 sales and volume > 0 -> SURGE
        res_held = monitor.evaluate_nft_surge(nft_item, [0.01, 0.02, 0.015], secondary_moves=10, user_held=2)
        self.assertTrue(res_held["is_nft_surge"])
        self.assertEqual(res_held["sales_count"], 3)
        self.assertAlmostEqual(res_held["vol_bnb"], 0.045, places=4)
        self.assertAlmostEqual(res_held["floor_price_bnb"], 0.01, places=4)
        self.assertAlmostEqual(res_held["high_price_bnb"], 0.02, places=4)
        self.assertIn("SURGE", res_held["badge"])

        # 3. A single sale is enough: any trading in the window marks an active collection
        res_held_cold = monitor.evaluate_nft_surge(nft_item, [0.01], secondary_moves=2, user_held=1)
        self.assertTrue(res_held_cold["is_nft_surge"])
        self.assertTrue(monitor.evaluate_nft_surge(nft_item, [0.000015])["is_nft_surge"])
        # ... and no sale at all is not, whatever was held, moved or seen before
        res_none = monitor.evaluate_nft_surge(dict(nft_item, secondary_sales_count=9), [], secondary_moves=40,
                                              user_held=3, prev_stats={"sales_count": 9})
        self.assertFalse(res_none["is_nft_surge"])
        self.assertEqual(res_none["badge"], "")

        # 4. Unheld item with 5 sales -> SURGE (Crowd traction)
        res_unheld_sales = monitor.evaluate_nft_surge(nft_item, [0.001, 0.001, 0.002, 0.001, 0.003], secondary_moves=15, user_held=0)
        self.assertTrue(res_unheld_sales["is_nft_surge"])
        self.assertEqual(res_unheld_sales["sales_count"], 5)

        # 5. Unheld item with 1 whale sale >= 0.5 BNB -> SURGE
        res_unheld_vol = monitor.evaluate_nft_surge(nft_item, [0.65], secondary_moves=4, user_held=0)
        self.assertTrue(res_unheld_vol["is_nft_surge"])

        # 6. Unheld item with 4 small sales (used to be below the 5-sale / 0.5 BNB bar) -> active
        res_unheld_sub = monitor.evaluate_nft_surge(nft_item, [0.01, 0.01, 0.01, 0.01], secondary_moves=5, user_held=0)
        self.assertTrue(res_unheld_sub["is_nft_surge"])

        # 7. 2x Volume spike vs previous check
        res_spike = monitor.evaluate_nft_surge(nft_item, [0.01]*6, secondary_moves=10, user_held=0, prev_stats={"sales_count": 3})
        self.assertTrue(res_spike["is_nft_surge"])

    def test_03_python_js_parity(self):
        """Test that Python evaluate_nft_surge and JS evaluateNftSurge produce identical results."""
        test_cases = [
            # (item, fills, moves, held, prev_stats)
            ({"standard": "ERC-721", "is_nft": True}, [0.02, 0.03, 0.01], 8, 1, None),
            ({"standard": "ERC-721", "is_nft": True}, [0.005, 0.005], 2, 0, None),
            ({"standard": "ERC-721", "is_nft": True}, [0.001, 0.002, 0.001, 0.003, 0.002], 20, 0, None),
            ({"standard": "ERC-721", "is_nft": True}, [0.75], 5, 0, None),
            ({"standard": "ERC-721", "is_nft": True}, [0.000015], 0, 0, None),          # one tiny sale: active
            ({"standard": "ERC-721", "is_nft": True}, [], 12, 3, {"sales_count": 4}),   # no sale: not active
            ({"standard": "BEP-20 Token (18 decimals)", "is_nft": False}, [0.1, 0.2, 0.3], 50, 100, None),
        ]

        js_script = """
        const { evaluateNftSurge } = require('./app.js');
        const fs = require('fs');
        const raw = process.argv.slice(1).find(a => a.startsWith('[') || a.startsWith('{'));
        const input = JSON.parse(raw);
        const results = input.map(tc => {
            const res = evaluateNftSurge(tc.item, tc.fills, tc.moves, tc.held, tc.prev);
            return {
                is_nft_surge: res.is_nft_surge,
                sales_count: res.sales_count,
                vol_bnb: Number(res.vol_bnb.toFixed(4)),
                floor_price_bnb: Number(res.floor_price_bnb.toFixed(4)),
                has_badge: Boolean(res.badge)
            };
        });
        console.log(JSON.stringify(results));
        """

        formatted_input = [
            {"item": tc[0], "fills": tc[1], "moves": tc[2], "held": tc[3], "prev": tc[4]}
            for tc in test_cases
        ]

        p = subprocess.run(
            ["node", "-e", js_script, json.dumps(formatted_input)],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True
        )
        self.assertEqual(p.returncode, 0, f"Node error: {p.stderr}")
        js_results = json.loads(p.stdout)

        for i, tc in enumerate(test_cases):
            py_res = monitor.evaluate_nft_surge(tc[0], tc[1], tc[2], tc[3], tc[4])
            js_res = js_results[i]

            self.assertEqual(py_res["is_nft_surge"], js_res["is_nft_surge"], f"Case {i} surge mismatch")
            self.assertEqual(py_res["sales_count"], js_res["sales_count"], f"Case {i} sales_count mismatch")
            self.assertAlmostEqual(py_res["vol_bnb"], js_res["vol_bnb"], places=3, msg=f"Case {i} vol mismatch")
            self.assertAlmostEqual(py_res["floor_price_bnb"], js_res["floor_price_bnb"], places=3, msg=f"Case {i} floor mismatch")
            self.assertEqual(bool(py_res["badge"]), js_res["has_badge"], f"Case {i} badge mismatch")

    def test_04_portfolio_exit_with_nft_surge(self):
        """Test portfolio exit signal triggers 🦋 ELEMENT SURGE for hot NFTs without auto-selling."""
        hot_nft = {
            "name": "Lazabit",
            "symbol": "LAZABIT",
            "contract": self.sample_col,
            "standard": "ERC-721 (NFT Collection)",
            "is_nft": True,
            "user_balance": 3,
            "is_nft_surge": True,
            "secondary_sales_count": 7,
            "floor_price_bnb": 0.0012,
            "secondary_vol_bnb": 0.0084
        }

        # Python evaluation
        py_eval = monitor.evaluate_portfolio_exit(hot_nft)
        self.assertEqual(py_eval["signal"], "🦋 ELEMENT SURGE")
        self.assertIn("7 sales", py_eval["badge"])
        self.assertFalse(py_eval["can_swap"])
        self.assertEqual(py_eval["action"], "LIST_ELEMENT")
        self.assertIn("manual", py_eval["recommendation"].lower())

        # JavaScript evaluation parity
        js_eval_script = """
        const { evaluatePortfolioExit } = require('./app.js');
        const raw = process.argv.slice(1).find(a => a.startsWith('[') || a.startsWith('{'));
        const item = JSON.parse(raw);
        const res = evaluatePortfolioExit(item, 705.0);
        console.log(JSON.stringify(res));
        """
        p = subprocess.run(
            ["node", "-e", js_eval_script, json.dumps(hot_nft)],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True
        )
        self.assertEqual(p.returncode, 0, f"Node error: {p.stderr}")
        js_eval = json.loads(p.stdout)

        self.assertEqual(js_eval["signal"], "🦋 ELEMENT SURGE")
        self.assertEqual(js_eval["action"], "LIST_ELEMENT")
        self.assertFalse(js_eval["can_swap"])
        self.assertIn("Element", js_eval["recommendation"])

    def test_05_nft_surge_filter_and_sort(self):
        """Test filter_and_sort_projects properly filters by NFT_SURGE."""
        mock_history = [
            {"contract": "0x1", "name": "Item 1", "is_nft_surge": True, "score": 80},
            {"contract": "0x2", "name": "Item 2", "is_nft_surge": False, "score": 90},
            {"contract": "0x3", "name": "Item 3", "is_nft_surge": True, "score": 60},
        ]

        filtered = monitor.filter_and_sort_projects(mock_history, "NFT_SURGE", "HEAT")
        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered[0]["contract"], "0x1")
        self.assertEqual(filtered[1]["contract"], "0x3")

    def test_06_live_element_volume_scan(self):
        """Test live scan_element_nft_volume against BSC mainnet RPC."""
        test_items = [
            {
                "contract": self.sample_col,
                "name": "Lazabit",
                "standard": "ERC-721 (NFT Collection)",
                "is_nft": True
            },
            {
                "contract": self.sample_nomio,
                "name": "Nomio",
                "standard": "ERC-721 (NFT Collection)",
                "is_nft": True
            }
        ]

        # Scan last 15000 blocks
        res = monitor.scan_element_nft_volume(self.rpc, test_items, lookback_blocks=15000)
        self.assertIsInstance(res, dict)
        
        # Verify both items were evaluated
        for it in test_items:
            c = it["contract"].lower()
            self.assertIn(c, res)
            eval_data = res[c]
            self.assertIn("sales_count", eval_data)
            self.assertIn("vol_bnb", eval_data)
            self.assertIn("is_nft_surge", eval_data)
            print(f"\n[LIVE BSC TEST] {it['name']}: {eval_data['sales_count']} sales, {eval_data['vol_bnb']:.4f} BNB vol, Surge={eval_data['is_nft_surge']}")

if __name__ == "__main__":
    unittest.main()
