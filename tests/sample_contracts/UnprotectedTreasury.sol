// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

contract UnprotectedTreasury {
    address public owner;

    constructor() {
        owner = msg.sender;
    }

    function withdrawAll() external {
        uint256 amount = address(this).balance;

        (bool ok, ) = payable(msg.sender).call{value: amount}("");
        require(ok, "transfer failed");
    }

    receive() external payable {}
}