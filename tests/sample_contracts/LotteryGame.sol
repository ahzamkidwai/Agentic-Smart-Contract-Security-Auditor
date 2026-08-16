// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

/// @notice Intentionally vulnerable lottery contract for testing the audit
/// pipeline against a different set of SWC categories than VulnerableBank
/// (SWC-107) and SafeVault (safe baseline).
///
/// Planted vulnerabilities:
///   1. Weak randomness (SWC-116 / SWC-120) — pickWinner() derives its
///      "random" winner from block.timestamp/block.prevrandao, both of
///      which a miner/validator can influence or predict.
///   2. Unchecked low-level call (SWC-104) — the payout in pickWinner()
///      does NOT check the call's return value, unlike SafeVault's
///      withdraw()/emergencyWithdraw(), which both do. This is the
///      genuinely-unchecked contrast case.
///   3. tx.origin authorization (SWC-115) — withdrawFees() gates access
///      with tx.origin instead of msg.sender, making it phishable via a
///      malicious contract the owner is tricked into interacting with.
///   4. Unprotected self-destruct (SWC-106) — destroy() has no access
///      control at all; anyone can call it and drain the contract.
contract LotteryGame {
    address public owner;
    address[] public players;
    uint256 public prizePool;

    constructor() {
        owner = msg.sender;
    }

    function enter() external payable {
        require(msg.value == 0.1 ether, "entry fee is 0.1 ether");
        players.push(msg.sender);
        prizePool += msg.value;
    }

    /// @notice Picks a winner and pays out the prize pool.
    function pickWinner() external {
        require(players.length > 0, "no players");

        // Vulnerability: predictable/manipulable "randomness".
        uint256 winnerIndex = uint256(
            keccak256(abi.encodePacked(block.timestamp, block.prevrandao, players.length))
        ) % players.length;

        address winner = players[winnerIndex];

        // Vulnerability: return value of the low-level call is never
        // checked. If the transfer silently fails (e.g. winner is a
        // contract that reverts on receive), prizePool and players are
        // still reset below as if payout succeeded — funds are lost.
        winner.call{value: prizePool}("");

        prizePool = 0;
        delete players;
    }

    /// @notice Withdraw accumulated fees.
    function withdrawFees(uint256 amount) external {
        // Vulnerability: tx.origin used for authorization instead of
        // msg.sender.
        require(tx.origin == owner, "not owner");
        payable(msg.sender).transfer(amount);
    }

    /// @notice Emergency shutdown.
    function destroy() external {
        // Vulnerability: no access control — anyone can call this.
        selfdestruct(payable(msg.sender));
    }
}
