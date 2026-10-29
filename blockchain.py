"""
WarrantyChain core ledger.

A minimal, self-contained hash-chained ledger (the same idea behind
public blockchains, without the network/consensus layer) used to record
every event in a product's life: MINT, TRANSFER, CLAIM_FILED,
CLAIM_RESOLVED. Each block stores the SHA-256 hash of the previous
block, so altering any historical block breaks every hash that follows
it -- that break is what "verify()" detects.
"""
import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

CHAIN_FILE = os.path.join(os.path.dirname(__file__), "data", "chain.json")


@dataclass
class Block:
    index: int
    timestamp: str
    event_type: str
    data: Dict[str, Any]
    previous_hash: str
    hash: str = field(default="")
    nonce: int = 0

    def compute_hash(self) -> str:
        payload = {
            "index": self.index,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "data": self.data,
            "previous_hash": self.previous_hash,
            "nonce": self.nonce,
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(encoded).hexdigest()


class Blockchain:
    """A single, append-only chain shared by every product on the platform."""

    DIFFICULTY_PREFIX = "0"  # tiny proof-of-work just to show the concept

    def __init__(self):
        self.chain: List[Block] = []
        self._load_or_genesis()

    # ---------- persistence ----------
    def _load_or_genesis(self):
        os.makedirs(os.path.dirname(CHAIN_FILE), exist_ok=True)
        if os.path.exists(CHAIN_FILE):
            with open(CHAIN_FILE, "r") as f:
                raw = json.load(f)
            self.chain = [Block(**b) for b in raw]
        else:
            genesis = Block(
                index=0,
                timestamp=self._now(),
                event_type="GENESIS",
                data={"message": "WarrantyChain genesis block"},
                previous_hash="0" * 64,
            )
            genesis.hash = self._mine(genesis)
            self.chain = [genesis]
            self._save()

    def _save(self):
        with open(CHAIN_FILE, "w") as f:
            json.dump([asdict(b) for b in self.chain], f, indent=2, default=str)

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _mine(self, block: Block) -> str:
        """Tiny proof-of-work: find a nonce whose hash starts with DIFFICULTY_PREFIX."""
        block.nonce = 0
        h = block.compute_hash()
        while not h.startswith(self.DIFFICULTY_PREFIX):
            block.nonce += 1
            h = block.compute_hash()
        return h

    # ---------- writing ----------
    def add_block(self, event_type: str, data: Dict[str, Any]) -> Block:
        last = self.chain[-1]
        block = Block(
            index=last.index + 1,
            timestamp=self._now(),
            event_type=event_type,
            data=data,
            previous_hash=last.hash,
        )
        block.hash = self._mine(block)
        self.chain.append(block)
        self._save()
        return block

    # ---------- reading ----------
    def history_for_serial(self, serial_number: str) -> List[Block]:
        return [
            b for b in self.chain
            if b.event_type != "GENESIS" and b.data.get("serial_number") == serial_number
        ]

    def is_valid(self) -> (bool, Optional[str]):
        """Re-computes every hash and link; returns (True, None) or (False, reason)."""
        for i in range(1, len(self.chain)):
            current, prev = self.chain[i], self.chain[i - 1]
            if current.previous_hash != prev.hash:
                return False, f"Block {current.index} previous_hash does not match block {prev.index}'s hash"
            if current.compute_hash() != current.hash:
                return False, f"Block {current.index} hash does not match its recomputed content (tampered)"
        return True, None

    def as_list(self) -> List[Dict[str, Any]]:
        return [asdict(b) for b in self.chain]


_instance: Optional[Blockchain] = None


def get_chain() -> Blockchain:
    global _instance
    if _instance is None:
        _instance = Blockchain()
    return _instance
