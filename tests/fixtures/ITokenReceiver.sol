// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Pure interface - used to test is_pure_interface() correctly skips this
// for Mythril while Slither still includes it for ERC-conformance checks.
interface ITokenReceiver {
    function onTokenReceived(address from, uint256 amount) external returns (bool);
}
