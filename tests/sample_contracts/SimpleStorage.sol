// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice A completely safe and simple storage contract.
contract SimpleStorage {
    uint256 public value;

    /// @notice Updates the stored value.
    /// @param newValue The new value to store.
    function set(uint256 newValue) external {
        value = newValue;
    }
}