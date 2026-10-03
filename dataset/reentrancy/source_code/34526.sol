pragma solidity ^0.4.13;

contract ERC20Basic {
  uint public totalSupply;
  function balanceOf(address who) constant public returns (uint);
  function transfer(address to, uint value) public;
  event Transfer(address indexed from, address indexed to, uint value);
}

contract ERC20 is ERC20Basic {
  function allowance(address owner, address spender) constant public returns (uint);
  function transferFrom(address from, address to, uint value) public;
  function approve(address spender, uint value) public;
  event Approval(address indexed owner, address indexed spender, uint value) ;
}

library SafeMath {
  function mul(uint a, uint b) internal pure returns (uint) {
    uint c = a * b;
    assert(a == 0 || c / a == b);
    return c;
  }

  function div(uint a, uint b) internal pure returns (uint) {

    uint c = a / b;

    return c;
  }

  function sub(uint a, uint b) internal pure returns (uint) {
    assert(b <= a);
    return a - b;
  }

  function add(uint a, uint b) internal pure returns (uint) {
    uint c = a + b;
    assert(c >= a);
    return c;
  }

  function max64(uint64 a, uint64 b) internal pure returns (uint64) {
    return a >= b ? a : b;
  }

  function min64(uint64 a, uint64 b) internal pure returns (uint64) {
    return a < b ? a : b;
  }

  function max256(uint256 a, uint256 b) internal pure returns (uint256) {
    return a >= b ? a : b;
  }

  function min256(uint256 a, uint256 b) internal pure returns (uint256) {
    return a < b ? a : b;
  }
}

contract BasicToken is ERC20Basic {
  using SafeMath for uint;

  mapping(address => uint) balances;

  modifier onlyPayloadSize(uint size) {
     if(msg.data.length < size + 4) {
       revert();
     }
     _;
  }

  function transfer(address _to, uint _value) onlyPayloadSize(2 * 32) public{
    balances[msg.sender] = balances[msg.sender].sub(_value);
    balances[_to] = balances[_to].add(_value);
    Transfer(msg.sender, _to, _value);
  }

  function balanceOf(address _owner) constant public returns (uint balance){
    return balances[_owner];
  }

}

contract StandardToken is BasicToken, ERC20 {

  mapping (address => mapping (address => uint)) allowed;

  function transferFrom(address _from, address _to, uint _value) onlyPayloadSize(3 * 32) public{
    var _allowance = allowed[_from][msg.sender];

    balances[_to] = balances[_to].add(_value);
    balances[_from] = balances[_from].sub(_value);
    allowed[_from][msg.sender] = _allowance.sub(_value);
    Transfer(_from, _to, _value);
  }

  function approve(address _spender, uint _value) public{

    if ((_value != 0) && (allowed[msg.sender][_spender] != 0)) revert();

    allowed[msg.sender][_spender] = _value;
    Approval(msg.sender, _spender, _value);
  }

  function allowance(address _owner, address _spender) constant public returns (uint remaining) {
    return allowed[_owner][_spender];
  }

}

contract IAMEToken is StandardToken {
	using SafeMath for uint256;

	event CreatedIAM(address indexed _creator, uint256 _amountOfIAM);
	event IAMRefundedForWei(address indexed _refunder, uint256 _amountOfWei);

	string public constant name = "IAME Token";
	string public constant symbol = "IAM";
	uint256 public constant decimals = 18;
	string public version = "1.0";

	address public executor;
	address public devETHDestination;
	address public reserveIAMDestination;

	bool public saleHasEnded;
	bool public minCapReached;
	bool public allowRefund;
	mapping (address => uint256) public ETHContributed;
	uint256 public totalETHRaised;
	uint256 public saleStartBlock;
	uint256 public saleEndBlock;
	uint256 public saleFirstPresaleEndBlock;
	uint256 public constant RESERVE_PORTION_MULTIPLIER = 1;
	uint256 public constant SECURITY_ETHER_CAP = 1000000 ether;
	uint256 public constant IAM_PER_ETH_BASE_RATE = 1000;
	uint256 public constant IAM_PER_ETH_PRE_SALE_RATE = 2000;
  uint256 public constant PRE_SALE_CAP = 6000000;

	function IAMEToken(
		address _devETHDestination,
		address _reserveIAMDestination,
		uint256 _saleStartBlock,
		uint256 _saleEndBlock
	) {

		if (_devETHDestination == address(0x0)) revert();
		if (_reserveIAMDestination == address(0x0)) revert();

		if (_saleEndBlock <= block.number) revert();

		if (_saleEndBlock <= _saleStartBlock) revert();

		executor = msg.sender;
		saleHasEnded = false;
		minCapReached = false;
		allowRefund = false;
		devETHDestination = _devETHDestination;
		reserveIAMDestination = _reserveIAMDestination;
		totalETHRaised = 0;
		saleStartBlock = _saleStartBlock;
		saleEndBlock = _saleEndBlock;
		saleFirstPresaleEndBlock = saleStartBlock + 62608;
		totalSupply = 0;
	}

	function () payable {

		if (saleHasEnded) revert();
		if (block.number < saleStartBlock) revert();
		if (block.number > saleEndBlock) revert();

		uint256 newEtherBalance = totalETHRaised.add(msg.value);
		if (newEtherBalance > SECURITY_ETHER_CAP) revert();

		if (0 == msg.value) revert();

		uint256 curTokenRate = IAM_PER_ETH_BASE_RATE;
		if (block.number < saleFirstPresaleEndBlock || totalSupply < PRE_SALE_CAP) {
		    curTokenRate = IAM_PER_ETH_PRE_SALE_RATE;
		}

		uint256 amountOfIAM = msg.value.mul(curTokenRate);

		uint256 totalSupplySafe = totalSupply.add(amountOfIAM);
		uint256 balanceSafe = balances[msg.sender].add(amountOfIAM);
		uint256 contributedSafe = ETHContributed[msg.sender].add(msg.value);

		totalSupply = totalSupplySafe;
		balances[msg.sender] = balanceSafe;

		totalETHRaised = newEtherBalance;
		ETHContributed[msg.sender] = contributedSafe;

		CreatedIAM(msg.sender, amountOfIAM);
	}

	function endSale() {

		if (saleHasEnded) revert();

		if (!minCapReached) revert();

		if (msg.sender != executor) revert();

		saleHasEnded = true;

		uint256 reserveShare = (totalSupply.mul(RESERVE_PORTION_MULTIPLIER));
		uint256 totalSupplySafe = totalSupply.add(reserveShare);

		totalSupply = totalSupplySafe;
		balances[reserveIAMDestination] = reserveShare;

		CreatedIAM(reserveIAMDestination, reserveShare);

		if (this.balance > 0) {
			if (!devETHDestination.call.value(this.balance)()) revert();
		}
	}

	function withdrawFunds() {

		if (!minCapReached) revert();
		if (0 == this.balance) revert();

		if (!devETHDestination.call.value(this.balance)()) revert();
	}

	function triggerMinCap() {
		if (msg.sender != executor) revert();

		minCapReached = true;
	}

	function triggerRefund() {

		if (saleHasEnded) revert();

		if (minCapReached) revert();

		if (block.number < saleEndBlock) revert();
		if (msg.sender != executor) revert();

		allowRefund = true;
	}

	function refund() external {

		if (!allowRefund) revert();

		if (0 == ETHContributed[msg.sender]) revert();

		uint256 etherAmount = ETHContributed[msg.sender];
		ETHContributed[msg.sender] = 0;

		IAMRefundedForWei(msg.sender, etherAmount);
		if (!msg.sender.send(etherAmount)) revert();
	}

	function changeDeveloperETHDestinationAddress(address _newAddress) {
		if (msg.sender != executor) revert();
		devETHDestination = _newAddress;
	}

	function changeReserveIAMDestinationAddress(address _newAddress) {
		if (msg.sender != executor) revert();
		reserveIAMDestination = _newAddress;
	}

	function transfer(address _to, uint _value) {

		if (!minCapReached) revert();

		super.transfer(_to, _value);
	}

	function transferFrom(address _from, address _to, uint _value) {

		if (!minCapReached) revert();

		super.transferFrom(_from, _to, _value);
	}
}
