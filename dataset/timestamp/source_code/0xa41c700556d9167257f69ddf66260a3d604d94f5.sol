pragma solidity >=0.4.21 <0.6.0;

contract CARBONTRAIL_RCAI {
    event RCAI(
        bytes32 info_concealed,
        bytes32 client_full,
        bytes32 address_full,
        address declared_by,
        address declared_for,
        uint status,
        uint timestamp,
        uint block
    );

    function newRCAI(bytes32 info_concealed, bytes32 client_full, bytes32 address_full, address declared_for, uint status) public  {
        emit RCAI(info_concealed, client_full, address_full, msg.sender, declared_for, status, block.timestamp, block.number);
    }

    event URCAI(
        bytes32 client_full,
        bytes32 address_full,
        address declared_by,
        address declared_for,
        uint status,
        uint timestamp,
        uint block
    );

function updateRCAI(bytes32 client_full, bytes32 address_full, address declared_for, uint status) public  {
        emit URCAI(client_full, address_full, msg.sender, declared_for, status, block.timestamp, block.number);
    }
}
