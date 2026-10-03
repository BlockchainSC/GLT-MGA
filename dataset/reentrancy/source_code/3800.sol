pragma solidity ^0.4.21;

contract SafeMath {

  function safeSub(uint256 x, uint256 y) internal pure returns (uint256) {
    uint256 z = x - y;
    assert(z <= x);
    return z;
  }

  function safeAdd(uint256 x, uint256 y) internal pure returns (uint256) {
    uint256 z = x + y;
    assert(z >= x);
    return z;
  }

  function safeDiv(uint256 x, uint256 y) internal pure returns (uint256) {
    uint256 z = x / y;
    return z;
  }

  function safeMul(uint256 x, uint256 y) internal pure returns (uint256) {
    uint256 z = x * y;
    assert(x == 0 || z / x == y);
    return z;
  }

  function min(uint256 x, uint256 y) internal pure returns (uint256) {
    uint256 z = x <= y ? x : y;
    return z;
  }

  function max(uint256 x, uint256 y) internal pure returns (uint256) {
    uint256 z = x >= y ? x : y;
    return z;
  }
}

contract Ownable {

  address public owner;
  address public newOwner;

  event OwnershipTransferred(address indexed _from, address indexed _to);

  function Ownable () public {
    owner = msg.sender;
  }

  modifier onlyOwner() {
    assert(msg.sender == owner);
    _;
  }

  function transferOwnership(address _newOwner) public onlyOwner {
    assert(_newOwner != address(0));
    newOwner = _newOwner;
  }

  function acceptOwnership() public {
    if (msg.sender == newOwner) {
      emit OwnershipTransferred(owner, newOwner);
      owner = newOwner;
    }
  }
}

contract ERC223 {
  uint public totalSupply;
  function balanceOf(address who) public view returns (uint);

  function name() public view returns (string _name);
  function symbol() public view returns (string _symbol);
  function decimals() public view returns (uint _decimals);
  function totalSupply() public view returns (uint256 _supply);

  function transfer(address to, uint value) public returns (bool ok);
  function transfer(address to, uint value, bytes data) public returns (bool ok);
  function transfer(address to, uint value, bytes data, string custom_fallback) public returns (bool ok);

  event Transfer(address indexed from, address indexed to, uint value, bytes indexed data);
}

contract ContractReceiver {
    function tokenFallback(address _from, uint _value, bytes _data) public returns (bool success);
}

contract StandardToken is ERC223, SafeMath{

  mapping(address => uint) balances;

  string public name;
  string public symbol;
  uint public decimals;
  uint256 public totalSupply;

  bool public stopped = false;

  modifier isRunning {
    assert(!stopped);
    _;
  }

  function name() public view returns (string _name) {
      return name;
  }

  function symbol() public view returns (string _symbol) {
      return symbol;
  }

  function decimals() public view returns (uint _decimals) {
      return decimals;
  }

  function totalSupply() public view returns (uint256 _totalSupply) {
      return totalSupply;
  }

  function transfer(address _to, uint _value, bytes _data, string _custom_fallback) public returns (bool success) {

    if(isContract(_to)) {
        if (balanceOf(msg.sender) < _value) revert();
        balances[msg.sender] = safeSub(balanceOf(msg.sender), _value);
        balances[_to] = safeAdd(balanceOf(_to), _value);
        assert(_to.call.value(0)(bytes4(keccak256(_custom_fallback)), msg.sender, _value, _data));
        emit Transfer(msg.sender, _to, _value, _data);
        return true;
    }
    else {
        return transferToAddress(_to, _value, _data);
    }
}

  function transfer(address _to, uint _value, bytes _data) public returns (bool success) {

    if(isContract(_to)) {
        return transferToContract(_to, _value, _data);
    }
    else {
        return transferToAddress(_to, _value, _data);
    }
}

  function transfer(address _to, uint _value) public returns (bool success) {

    bytes memory empty;
    if(isContract(_to)) {
        return transferToContract(_to, _value, empty);
    }
    else {
        return transferToAddress(_to, _value, empty);
    }
}

  function isContract(address _addr) private view returns (bool is_contract) {
      uint length;
      assembly {

            length := extcodesize(_addr)
      }
      return (length>0);
    }

  function transferToAddress(address _to, uint _value, bytes _data) private returns (bool success) {
    if (balanceOf(msg.sender) < _value) revert();
    balances[msg.sender] = safeSub(balanceOf(msg.sender), _value);
    balances[_to] = safeAdd(balanceOf(_to), _value);
    emit Transfer(msg.sender, _to, _value, _data);
    return true;
  }

  function transferToContract(address _to, uint _value, bytes _data) private returns (bool success) {
    if (balanceOf(msg.sender) < _value) revert();
    balances[msg.sender] = safeSub(balanceOf(msg.sender), _value);
    balances[_to] = safeAdd(balanceOf(_to), _value);
    ContractReceiver receiver = ContractReceiver(_to);
    receiver.tokenFallback(msg.sender, _value, _data);
    emit Transfer(msg.sender, _to, _value, _data);
    return true;
  }

  function balanceOf(address _owner) public view returns (uint balance) {
    return balances[_owner];
  }
}

contract YouGive is StandardToken, Ownable {

  string public name;
  string public symbol;
  uint public decimals;

  event UpdatedTokenInformation(string newName, string newSymbol);

  function YouGive(uint256 _initialSupply, uint _decimals, string _name, string _symbol, address _addressFounder) public {

    totalSupply = _initialSupply;
    decimals = _decimals;
    name = _name;
    symbol = _symbol;

    balances[_addressFounder] = totalSupply;
    bytes memory empty;
    emit Transfer(0x0, _addressFounder, balances[_addressFounder], empty);
  }

  function stop() public onlyOwner {
    stopped = true;
  }

  function start() public onlyOwner {
    stopped = false;
  }

  function setTokenInformation(string _name, string _symbol, uint256 totalSupply_) public onlyOwner {
    name = _name;
    symbol = _symbol;
    totalSupply = totalSupply_;
    emit UpdatedTokenInformation(name, symbol);
  }
}
