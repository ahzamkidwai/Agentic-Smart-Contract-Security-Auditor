// SPDX-License-Identifier: MIT
pragma solidity ^0.8.10;

/// @notice Deliberately vulnerable toy contract used to exercise the
/// audit-explainer pipeline end to end. Do NOT deploy this anywhere real.
contract VulnerableBank {
    mapping(address => uint256) public balances;
    address public owner;

    constructor() {
        owner = msg.sender;
    }

    function deposit() external payable {
        balances[msg.sender] += msg.value;
    }

    /// SWC-107 (Reentrancy): sends ETH before zeroing out the balance,
    /// so a malicious contract can re-enter withdraw() from its receive()
    /// hook and drain funds before balances[msg.sender] is ever updated.
    function withdraw(uint256 amount) external {
        require(balances[msg.sender] >= amount, "insufficient balance");

        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");

        balances[msg.sender] -= amount;
    }

    /// SWC-115 (tx.origin): authorization check uses tx.origin instead of
    /// msg.sender, so a phishing contract calling this on the owner's
    /// behalf can pass the check.
    function emergencyWithdrawAll() external {
        require(tx.origin == owner, "not owner");
        (bool ok, ) = owner.call{value: address(this).balance}("");
        require(ok, "transfer failed");
    }

    /// SWC-105 (Unprotected Ether Withdrawal): no access control at all.
    function sweep(address payable to) external {
        to.transfer(address(this).balance);
    }

    receive() external payable {}
}
