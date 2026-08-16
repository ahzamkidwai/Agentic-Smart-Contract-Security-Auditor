// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

contract TxOriginWallet {
    address public owner;

    constructor() {
        owner = msg.sender;
    }

    function withdraw(uint256 amount) external {
        require(tx.origin == owner, "not owner");

        (bool ok, ) = payable(msg.sender).call{value: amount}("");
        require(ok, "transfer failed");
    }

    receive() external payable {}
}