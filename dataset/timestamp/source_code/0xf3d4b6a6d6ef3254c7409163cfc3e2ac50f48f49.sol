pragma solidity 0.4.24;

contract FeedBbt {

    address public owner;

    uint public contentCount = 0;
    uint public fee = 1;

    event Feed(uint indexed version, uint indexed timePage, uint indexed payment, string dataInfo);

    modifier onlyOwner {
        require(msg.sender == owner);
        _;
    }

    constructor() public {
        owner = msg.sender;
    }

    function () public {
        revert();
    }

    function kill() public onlyOwner {
        selfdestruct(owner);
    }

    function add(uint _version, uint _fee, string _dataInfo) public onlyOwner {
        contentCount++;
        fee = _fee;
        emit Feed(_version, block.timestamp / (1 days), _fee, _dataInfo);
    }
}
