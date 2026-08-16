// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

/// @notice Simple, safe vault contract for testing the audit pipeline.
contract SafeVault {
    mapping(address => uint256) public balances;
    address public owner;

    constructor() {
        owner = msg.sender;
    }

    function deposit() external payable {
        balances[msg.sender] += msg.value;
    }

    function withdraw(uint256 amount) external {
        require(balances[msg.sender] >= amount, "insufficient balance");
        balances[msg.sender] -= amount; // state update before external call
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    /// @notice Owner can withdraw all funds in an emergency.
    function emergencyWithdraw() external onlyOwner {
        uint256 amount = address(this).balance;
        (bool ok, ) = owner.call{value: amount}("");
        require(ok, "transfer failed");
    }
}