pragma solidity ^0.4.23;

library SafeMath {

  function mul(uint256 a, uint256 b) internal pure returns (uint256 c) {
    if (a == 0) {
      return 0;
    }
    c = a * b;
    assert(c / a == b);
    return c;
  }

  function div(uint256 a, uint256 b) internal pure returns (uint256) {

    return a / b;
  }

  function sub(uint256 a, uint256 b) internal pure returns (uint256) {
    assert(b <= a);
    return a - b;
  }

  function add(uint256 a, uint256 b) internal pure returns (uint256 c) {
    c = a + b;
    assert(c >= a);
    return c;
  }
}

contract ERC20Basic {
  function totalSupply() public view returns (uint256);
  function balanceOf(address who) public view returns (uint256);
  function transfer(address to, uint256 value) public returns (bool);
  event Transfer(address indexed from, address indexed to, uint256 value);
}

contract ERC20 is ERC20Basic {
  function allowance(address owner, address spender) public view returns (uint256);
  function transferFrom(address from, address to, uint256 value) public returns (bool);
  function approve(address spender, uint256 value) public returns (bool);
  event Approval(address indexed owner, address indexed spender, uint256 value);
}

contract Registry {
    struct AttributeData {
        uint256 value;
        bytes32 notes;
        address adminAddr;
        uint256 timestamp;
    }

    address public owner;
    address public pendingOwner;
    bool public initialized;

    mapping(address => mapping(bytes32 => AttributeData)) public attributes;

    bytes32 public constant WRITE_PERMISSION = keccak256("canWriteTo-");

    event OwnershipTransferred(
        address indexed previousOwner,
        address indexed newOwner
    );
    event SetAttribute(address indexed who, bytes32 attribute, uint256 value, bytes32 notes, address indexed adminAddr);
    event SetManager(address indexed oldManager, address indexed newManager);

    function initialize() public {
        require(!initialized, "already initialized");
        owner = msg.sender;
        initialized = true;
    }

    function writeAttributeFor(bytes32 _attribute) public pure returns (bytes32) {
        return keccak256(WRITE_PERMISSION ^ _attribute);
    }

    function confirmWrite(bytes32 _attribute, address _admin) public view returns (bool) {
        return (_admin == owner || hasAttribute(_admin, keccak256(WRITE_PERMISSION ^ _attribute)));
    }

    function setAttribute(address _who, bytes32 _attribute, uint256 _value, bytes32 _notes) public {
        require(confirmWrite(_attribute, msg.sender));
        attributes[_who][_attribute] = AttributeData(_value, _notes, msg.sender, block.timestamp);
        emit SetAttribute(_who, _attribute, _value, _notes, msg.sender);
    }

    function setAttributeValue(address _who, bytes32 _attribute, uint256 _value) public {
        require(confirmWrite(_attribute, msg.sender));
        attributes[_who][_attribute] = AttributeData(_value, "", msg.sender, block.timestamp);
        emit SetAttribute(_who, _attribute, _value, "", msg.sender);
    }

    function hasAttribute(address _who, bytes32 _attribute) public view returns (bool) {
        return attributes[_who][_attribute].value != 0;
    }

    function hasBothAttributes(address _who, bytes32 _attribute1, bytes32 _attribute2) public view returns (bool) {
        return attributes[_who][_attribute1].value != 0 && attributes[_who][_attribute2].value != 0;
    }

    function hasEitherAttribute(address _who, bytes32 _attribute1, bytes32 _attribute2) public view returns (bool) {
        return attributes[_who][_attribute1].value != 0 || attributes[_who][_attribute2].value != 0;
    }

    function hasAttribute1ButNotAttribute2(address _who, bytes32 _attribute1, bytes32 _attribute2) public view returns (bool) {
        return attributes[_who][_attribute1].value != 0 && attributes[_who][_attribute2].value == 0;
    }

    function bothHaveAttribute(address _who1, address _who2, bytes32 _attribute) public view returns (bool) {
        return attributes[_who1][_attribute].value != 0 && attributes[_who2][_attribute].value != 0;
    }

    function eitherHaveAttribute(address _who1, address _who2, bytes32 _attribute) public view returns (bool) {
        return attributes[_who1][_attribute].value != 0 || attributes[_who2][_attribute].value != 0;
    }

    function haveAttributes(address _who1, bytes32 _attribute1, address _who2, bytes32 _attribute2) public view returns (bool) {
        return attributes[_who1][_attribute1].value != 0 && attributes[_who2][_attribute2].value != 0;
    }

    function haveEitherAttribute(address _who1, bytes32 _attribute1, address _who2, bytes32 _attribute2) public view returns (bool) {
        return attributes[_who1][_attribute1].value != 0 || attributes[_who2][_attribute2].value != 0;
    }

    function getAttribute(address _who, bytes32 _attribute) public view returns (uint256, bytes32, address, uint256) {
        AttributeData memory data = attributes[_who][_attribute];
        return (data.value, data.notes, data.adminAddr, data.timestamp);
    }

    function getAttributeValue(address _who, bytes32 _attribute) public view returns (uint256) {
        return attributes[_who][_attribute].value;
    }

    function getAttributeAdminAddr(address _who, bytes32 _attribute) public view returns (address) {
        return attributes[_who][_attribute].adminAddr;
    }

    function getAttributeTimestamp(address _who, bytes32 _attribute) public view returns (uint256) {
        return attributes[_who][_attribute].timestamp;
    }

    function reclaimEther(address _to) external onlyOwner {
        _to.transfer(address(this).balance);
    }

    function reclaimToken(ERC20 token, address _to) external onlyOwner {
        uint256 balance = token.balanceOf(this);
        token.transfer(_to, balance);
    }

    constructor() public {
        owner = msg.sender;
        emit OwnershipTransferred(address(0), owner);
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "only Owner");
        _;
    }

    modifier onlyPendingOwner() {
        require(msg.sender == pendingOwner);
        _;
    }

    function transferOwnership(address newOwner) public onlyOwner {
        pendingOwner = newOwner;
    }

    function claimOwnership() public onlyPendingOwner {
        emit OwnershipTransferred(owner, pendingOwner);
        owner = pendingOwner;
        pendingOwner = address(0);
    }
}

contract Ownable {
  address public owner;

  event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

  function Ownable() public {
    owner = msg.sender;
  }

  modifier onlyOwner() {
    require(msg.sender == owner);
    _;
  }

  function transferOwnership(address newOwner) public onlyOwner {
    require(newOwner != address(0));
    emit OwnershipTransferred(owner, newOwner);
    owner = newOwner;
  }

}

contract Claimable is Ownable {
  address public pendingOwner;

  modifier onlyPendingOwner() {
    require(msg.sender == pendingOwner);
    _;
  }

  function transferOwnership(address newOwner) onlyOwner public {
    pendingOwner = newOwner;
  }

  function claimOwnership() onlyPendingOwner public {
    emit OwnershipTransferred(owner, pendingOwner);
    owner = pendingOwner;
    pendingOwner = address(0);
  }
}

contract BalanceSheet is Claimable {
    using SafeMath for uint256;

    mapping (address => uint256) public balanceOf;

    function addBalance(address _addr, uint256 _value) public onlyOwner {
        balanceOf[_addr] = balanceOf[_addr].add(_value);
    }

    function subBalance(address _addr, uint256 _value) public onlyOwner {
        balanceOf[_addr] = balanceOf[_addr].sub(_value);
    }

    function setBalance(address _addr, uint256 _value) public onlyOwner {
        balanceOf[_addr] = _value;
    }
}

contract AllowanceSheet is Claimable {
    using SafeMath for uint256;

    mapping (address => mapping (address => uint256)) public allowanceOf;

    function addAllowance(address _tokenHolder, address _spender, uint256 _value) public onlyOwner {
        allowanceOf[_tokenHolder][_spender] = allowanceOf[_tokenHolder][_spender].add(_value);
    }

    function subAllowance(address _tokenHolder, address _spender, uint256 _value) public onlyOwner {
        allowanceOf[_tokenHolder][_spender] = allowanceOf[_tokenHolder][_spender].sub(_value);
    }

    function setAllowance(address _tokenHolder, address _spender, uint256 _value) public onlyOwner {
        allowanceOf[_tokenHolder][_spender] = _value;
    }
}

contract GlobalPause is Claimable {
    bool public allTokensPaused = false;
    string public pauseNotice;

    function pauseAllTokens(bool _status, string _notice) public onlyOwner {
        allTokensPaused = _status;
        pauseNotice = _notice;
    }

    function requireNotPaused() public view {
        require(!allTokensPaused, pauseNotice);
    }
}

contract ProxyStorage {
    address public owner;
    address public pendingOwner;

    bool public initialized;

    BalanceSheet public balances;
    AllowanceSheet public allowances;

    uint256 totalSupply_;

    bool public paused = false;
    GlobalPause public globalPause;

    uint256 public burnMin = 0;
    uint256 public burnMax = 0;

    Registry public registry;

    string public name = "TrueUSD";
    string public symbol = "TUSD";

    uint[] public gasRefundPool;
    uint256 public redemptionAddressCount;
}

contract HasOwner is ProxyStorage {

    event OwnershipTransferred(
        address indexed previousOwner,
        address indexed newOwner
    );

    constructor() public {
        owner = msg.sender;
        emit OwnershipTransferred(address(0), owner);
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "only Owner");
        _;
    }

    modifier onlyPendingOwner() {
        require(msg.sender == pendingOwner);
        _;
    }

    function transferOwnership(address newOwner) public onlyOwner {
        pendingOwner = newOwner;
    }

    function claimOwnership() public onlyPendingOwner {
        emit OwnershipTransferred(owner, pendingOwner);
        owner = pendingOwner;
        pendingOwner = address(0);
    }
}

contract ModularBasicToken is HasOwner {
    using SafeMath for uint256;

    event BalanceSheetSet(address indexed sheet);
    event Transfer(address indexed from, address indexed to, uint256 value);

    function setBalanceSheet(address _sheet) public onlyOwner returns (bool) {
        balances = BalanceSheet(_sheet);
        balances.claimOwnership();
        emit BalanceSheetSet(_sheet);
        return true;
    }

    function totalSupply() public view returns (uint256) {
        return totalSupply_;
    }

    function transfer(address _to, uint256 _value) public returns (bool) {
        _transferAllArgs(msg.sender, _to, _value);
        return true;
    }

    function _transferAllArgs(address _from, address _to, uint256 _value) internal {

        balances.subBalance(_from, _value);
        balances.addBalance(_to, _value);
        emit Transfer(_from, _to, _value);
    }

    function balanceOf(address _owner) public view returns (uint256 balance) {
        return balances.balanceOf(_owner);
    }
}

contract ModularStandardToken is ModularBasicToken {

    event AllowanceSheetSet(address indexed sheet);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    function setAllowanceSheet(address _sheet) public onlyOwner returns(bool) {
        allowances = AllowanceSheet(_sheet);
        allowances.claimOwnership();
        emit AllowanceSheetSet(_sheet);
        return true;
    }

    function transferFrom(address _from, address _to, uint256 _value) public returns (bool) {
        _transferFromAllArgs(_from, _to, _value, msg.sender);
        return true;
    }

    function _transferFromAllArgs(address _from, address _to, uint256 _value, address _spender) internal {
        require(_value <= allowances.allowanceOf(_from, _spender),"not enough allowance to transfer");

        _transferAllArgs(_from, _to, _value);
        allowances.subAllowance(_from, _spender, _value);
    }

    function approve(address _spender, uint256 _value) public returns (bool) {
        _approveAllArgs(_spender, _value, msg.sender);
        return true;
    }

    function _approveAllArgs(address _spender, uint256 _value, address _tokenHolder) internal {
        allowances.setAllowance(_tokenHolder, _spender, _value);
        emit Approval(_tokenHolder, _spender, _value);
    }

    function allowance(address _owner, address _spender) public view returns (uint256) {
        return allowances.allowanceOf(_owner, _spender);
    }

    function increaseApproval(address _spender, uint _addedValue) public returns (bool) {
        _increaseApprovalAllArgs(_spender, _addedValue, msg.sender);
        return true;
    }

    function _increaseApprovalAllArgs(address _spender, uint256 _addedValue, address _tokenHolder) internal {
        allowances.addAllowance(_tokenHolder, _spender, _addedValue);
        emit Approval(_tokenHolder, _spender, allowances.allowanceOf(_tokenHolder, _spender));
    }

    function decreaseApproval(address _spender, uint _subtractedValue) public returns (bool) {
        _decreaseApprovalAllArgs(_spender, _subtractedValue, msg.sender);
        return true;
    }

    function _decreaseApprovalAllArgs(address _spender, uint256 _subtractedValue, address _tokenHolder) internal {
        uint256 oldValue = allowances.allowanceOf(_tokenHolder, _spender);
        if (_subtractedValue > oldValue) {
            allowances.setAllowance(_tokenHolder, _spender, 0);
        } else {
            allowances.subAllowance(_tokenHolder, _spender, _subtractedValue);
        }
        emit Approval(_tokenHolder,_spender, allowances.allowanceOf(_tokenHolder, _spender));
    }
}

contract ModularBurnableToken is ModularStandardToken {
    event Burn(address indexed burner, uint256 value);

    function burn(uint256 _value) public {
        _burnAllArgs(msg.sender, _value);
    }

    function _burnAllArgs(address _burner, uint256 _value) internal {
        require(_value <= balances.balanceOf(_burner), "not enough balance to burn");

        balances.subBalance(_burner, _value);
        totalSupply_ = totalSupply_.sub(_value);
        emit Burn(_burner, _value);
        emit Transfer(_burner, address(0), _value);
    }
}

contract ModularMintableToken is ModularBurnableToken {
    event Mint(address indexed to, uint256 value);

    function mint(address _to, uint256 _value) public onlyOwner {
        require(_to != address(0), "to address cannot be zero");
        totalSupply_ = totalSupply_.add(_value);
        balances.addBalance(_to, _value);
        emit Mint(_to, _value);
        emit Transfer(address(0), _to, _value);
    }
}

contract ModularPausableToken is ModularMintableToken {

    event Pause();
    event Unpause();
    event GlobalPauseSet(address indexed newGlobalPause);

    modifier whenNotPaused() {
        require(!paused, "Token Paused");
        _;
    }

    modifier whenPaused() {
        require(paused, "Token Not Paused");
        _;
    }

    function pause() public onlyOwner whenNotPaused {
        paused = true;
        emit Pause();
    }

    function unpause() public onlyOwner whenPaused {
        paused = false;
        emit Unpause();
    }

    modifier onSupportedChain() {
        globalPause.requireNotPaused();
        _;
    }

    function setGlobalPause(address _newGlobalPause) external onlyOwner {
        globalPause = GlobalPause(_newGlobalPause);
        emit GlobalPauseSet(_newGlobalPause);
    }

    function _transferAllArgs(address _from, address _to, uint256 _value) internal whenNotPaused onSupportedChain {
        super._transferAllArgs(_from, _to, _value);
    }

    function _transferFromAllArgs(address _from, address _to, uint256 _value, address _spender) internal whenNotPaused onSupportedChain {
        super._transferFromAllArgs(_from, _to, _value, _spender);
    }

    function _approveAllArgs(address _spender, uint256 _value, address _tokenHolder) internal whenNotPaused onSupportedChain {
        super._approveAllArgs(_spender, _value, _tokenHolder);
    }

    function _increaseApprovalAllArgs(address _spender, uint256 _addedValue, address _tokenHolder) internal whenNotPaused onSupportedChain {
        super._increaseApprovalAllArgs(_spender, _addedValue, _tokenHolder);
    }

    function _decreaseApprovalAllArgs(address _spender, uint256 _subtractedValue, address _tokenHolder) internal whenNotPaused onSupportedChain {
        super._decreaseApprovalAllArgs(_spender, _subtractedValue, _tokenHolder);
    }

    function _burnAllArgs(address _burner, uint256 _value) internal whenNotPaused onSupportedChain {
        super._burnAllArgs(_burner, _value);
    }
}

contract BurnableTokenWithBounds is ModularPausableToken {

    event SetBurnBounds(uint256 newMin, uint256 newMax);

    function _burnAllArgs(address _burner, uint256 _value) internal {
        require(_value >= burnMin, "below min burn bound");
        require(_value <= burnMax, "exceeds max burn bound");
        super._burnAllArgs(_burner, _value);
    }

    function setBurnBounds(uint256 _min, uint256 _max) public onlyOwner {
        require(_min <= _max, "min > max");
        burnMin = _min;
        burnMax = _max;
        emit SetBurnBounds(_min, _max);
    }
}

contract CompliantToken is ModularPausableToken {

    bytes32 public constant HAS_PASSED_KYC_AML = "hasPassedKYC/AML";

    bytes32 public constant CAN_BURN = "canBurn";

    bytes32 public constant IS_BLACKLISTED = "isBlacklisted";

    event WipeBlacklistedAccount(address indexed account, uint256 balance);
    event SetRegistry(address indexed registry);

    function setRegistry(Registry _registry) public onlyOwner {
        registry = _registry;
        emit SetRegistry(registry);
    }

    function mint(address _to, uint256 _value) public onlyOwner {
        require(registry.hasAttribute1ButNotAttribute2(_to, HAS_PASSED_KYC_AML, IS_BLACKLISTED), "_to cannot mint");
        super.mint(_to, _value);
    }

    function _burnAllArgs(address _burner, uint256 _value) internal {
        require(registry.hasAttribute1ButNotAttribute2(_burner, CAN_BURN, IS_BLACKLISTED), "_burner cannot burn");
        super._burnAllArgs(_burner, _value);
    }

    function _transferFromAllArgs(address _from, address _to, uint256 _value, address _spender) internal {
        require(!registry.hasAttribute(_spender, IS_BLACKLISTED), "_spender is blacklisted");
        super._transferFromAllArgs(_from, _to, _value, _spender);
    }

    function _transferAllArgs(address _from, address _to, uint256 _value) internal {
        require(!registry.eitherHaveAttribute(_from, _to, IS_BLACKLISTED), "blacklisted");
        super._transferAllArgs(_from, _to, _value);
    }

    function wipeBlacklistedAccount(address _account) public onlyOwner {
        require(registry.hasAttribute(_account, IS_BLACKLISTED), "_account is not blacklisted");
        uint256 oldValue = balanceOf(_account);
        balances.setBalance(_account, 0);
        totalSupply_ = totalSupply_.sub(oldValue);
        emit WipeBlacklistedAccount(_account, oldValue);
        emit Transfer(_account, address(0), oldValue);
    }
}

contract RedeemableToken is ModularPausableToken {

    event RedemptionAddress(address indexed addr);

    function _transferAllArgs(address _from, address _to, uint256 _value) internal {
        if (_to == address(0)) {

            _burnAllArgs(_from, _value);
        } else if (uint(_to) <= redemptionAddressCount) {

            super._transferAllArgs(_from, _to, _value);
            _burnAllArgs(_to, _value);
        } else {
            super._transferAllArgs(_from, _to, _value);
        }
    }

    function _transferFromAllArgs(address _from, address _to, uint256 _value, address _spender) internal {
        require(_to != address(0), "_to address is 0x0");
        super._transferFromAllArgs(_from, _to, _value, _spender);
    }

    function incrementRedemptionAddressCount() external onlyOwner {
        emit RedemptionAddress(address(redemptionAddressCount));
        redemptionAddressCount += 1;
    }
}

contract DepositToken is ModularPausableToken {

    bytes32 public constant IS_DEPOSIT_ADDRESS = "isDepositAddress";

    function _transferAllArgs(address _from, address _to, uint256 _value) internal {
        address shiftedAddress = address(uint(_to) >> 20);
        uint depositAddressValue = registry.getAttributeValue(shiftedAddress, IS_DEPOSIT_ADDRESS);
        if (depositAddressValue != 0) {
            super._transferAllArgs(_from, _to, _value);
            super._transferAllArgs(_to, address(depositAddressValue), _value);
        } else {
            super._transferAllArgs(_from, _to, _value);
        }
    }

    function mint(address _to, uint256 _value) public onlyOwner {
        address shiftedAddress = address(uint(_to) >> 20);
        uint depositAddressValue = registry.getAttributeValue(shiftedAddress, IS_DEPOSIT_ADDRESS);
        if (depositAddressValue != 0) {
            super.mint(_to, _value);
            super._transferAllArgs(_to, address(depositAddressValue), _value);
        } else {
            super.mint(_to, _value);
        }
    }
}

contract GasRefundToken is ModularPausableToken {

    function sponsorGas() external {
        uint256 len = gasRefundPool.length;
        gasRefundPool.length = len + 9;
        gasRefundPool[len] = 1;
        gasRefundPool[len + 1] = 1;
        gasRefundPool[len + 2] = 1;
        gasRefundPool[len + 3] = 1;
        gasRefundPool[len + 4] = 1;
        gasRefundPool[len + 5] = 1;
        gasRefundPool[len + 6] = 1;
        gasRefundPool[len + 7] = 1;
        gasRefundPool[len + 8] = 1;
    }

    modifier gasRefund {
        uint256 len = gasRefundPool.length;
        if (len != 0) {
            gasRefundPool[--len] = 0;
            gasRefundPool[--len] = 0;
            gasRefundPool[--len] = 0;
            gasRefundPool.length = len;
        }
        _;
    }

    function remainingGasRefundPool() public view returns(uint) {
        return gasRefundPool.length;
    }

    function _transferAllArgs(address _from, address _to, uint256 _value) internal gasRefund {
        super._transferAllArgs(_from, _to, _value);
    }

    function mint(address _to, uint256 _value) public onlyOwner gasRefund {
        super.mint(_to, _value);
    }
}

contract TrueCoinReceiver {
    function tokenFallback( address from, uint256 value ) external;
}

contract TokenWithHook is ModularPausableToken {

    bytes32 public constant IS_REGISTERED_CONTRACT = "isRegisteredContract";

    function _transferAllArgs(address _from, address _to, uint256 _value) internal {
        uint length;
        assembly { length := extcodesize(_to) }
        super._transferAllArgs(_from, _to, _value);
        if (length > 0) {
            if(registry.hasAttribute(_to, IS_REGISTERED_CONTRACT)) {
                TrueCoinReceiver(_to).tokenFallback(_from, _value);
            }
        }
    }
}

contract DelegateERC20 is ModularStandardToken {

    address public constant DELEGATE_FROM = 0x8dd5fbCe2F6a956C3022bA3663759011Dd51e73E;

    modifier onlyDelegateFrom() {
        require(msg.sender == DELEGATE_FROM);
        _;
    }

    function delegateTotalSupply() public view returns (uint256) {
        return totalSupply();
    }

    function delegateBalanceOf(address who) public view returns (uint256) {
        return balanceOf(who);
    }

    function delegateTransfer(address to, uint256 value, address origSender) public onlyDelegateFrom returns (bool) {
        _transferAllArgs(origSender, to, value);
        return true;
    }

    function delegateAllowance(address owner, address spender) public view returns (uint256) {
        return allowance(owner, spender);
    }

    function delegateTransferFrom(address from, address to, uint256 value, address origSender) public onlyDelegateFrom returns (bool) {
        _transferFromAllArgs(from, to, value, origSender);
        return true;
    }

    function delegateApprove(address spender, uint256 value, address origSender) public onlyDelegateFrom returns (bool) {
        _approveAllArgs(spender, value, origSender);
        return true;
    }

    function delegateIncreaseApproval(address spender, uint addedValue, address origSender) public onlyDelegateFrom returns (bool) {
        _increaseApprovalAllArgs(spender, addedValue, origSender);
        return true;
    }

    function delegateDecreaseApproval(address spender, uint subtractedValue, address origSender) public onlyDelegateFrom returns (bool) {
        _decreaseApprovalAllArgs(spender, subtractedValue, origSender);
        return true;
    }
}

contract TrueUSD is
ModularPausableToken,
BurnableTokenWithBounds,
CompliantToken,
RedeemableToken,
TokenWithHook,
DelegateERC20,
DepositToken,
GasRefundToken {
    using SafeMath for *;

    uint8 public constant DECIMALS = 18;
    uint8 public constant ROUNDING = 2;

    event ChangeTokenName(string newName, string newSymbol);

    function initialize(uint256 _totalSupply) public {
        require(!initialized, "already initialized");
        initialized = true;
        owner = msg.sender;
        totalSupply_ = _totalSupply;
        burnMin = 10000 * 10**uint256(DECIMALS);
        burnMax = 20000000 * 10**uint256(DECIMALS);
        name = "TrueUSD";
        symbol = "TUSD";
    }

    function changeTokenName(string _name, string _symbol) external onlyOwner {
        name = _name;
        symbol = _symbol;
        emit ChangeTokenName(_name, _symbol);
    }

    function reclaimEther(address _to) external onlyOwner {
        _to.transfer(address(this).balance);
    }

    function reclaimToken(ERC20 token, address _to) external onlyOwner {
        uint256 balance = token.balanceOf(this);
        token.transfer(_to, balance);
    }

    function reclaimContract(Ownable _ownable) external onlyOwner {
        _ownable.transferOwnership(owner);
    }

    function _burnAllArgs(address _burner, uint256 _value) internal {

        uint burnAmount = _value.div(10 ** uint256(DECIMALS - ROUNDING)).mul(10 ** uint256(DECIMALS - ROUNDING));
        super._burnAllArgs(_burner, burnAmount);
    }
}

contract Proxy {

    function implementation() public view returns (address);

    function() external payable {
        address _impl = implementation();
        require(_impl != address(0), "implementation contract not set");

        assembly {
            let ptr := mload(0x40)
            calldatacopy(ptr, 0, calldatasize)
            let result := delegatecall(gas, _impl, ptr, calldatasize, 0, 0)
            let size := returndatasize
            returndatacopy(ptr, 0, size)

            switch result
            case 0 { revert(ptr, size) }
            default { return(ptr, size) }
        }
    }
}

contract UpgradeabilityProxy is Proxy {

    event Upgraded(address indexed implementation);

    bytes32 private constant implementationPosition = keccak256("trueUSD.proxy.implementation");

    function implementation() public view returns (address impl) {
        bytes32 position = implementationPosition;
        assembly {
          impl := sload(position)
        }
    }

    function _setImplementation(address newImplementation) internal {
        bytes32 position = implementationPosition;
        assembly {
          sstore(position, newImplementation)
        }
    }

    function _upgradeTo(address newImplementation) internal {
        address currentImplementation = implementation();
        require(currentImplementation != newImplementation);
        _setImplementation(newImplementation);
        emit Upgraded(newImplementation);
    }
}

contract OwnedUpgradeabilityProxy is UpgradeabilityProxy {

    event ProxyOwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    event NewPendingOwner(address currentOwner, address pendingOwner);

    bytes32 private constant proxyOwnerPosition = keccak256("trueUSD.proxy.owner");
    bytes32 private constant pendingProxyOwnerPosition = keccak256("trueUSD.pending.proxy.owner");

    constructor() public {
        _setUpgradeabilityOwner(msg.sender);
    }

    modifier onlyProxyOwner() {
        require(msg.sender == proxyOwner(), "only Proxy Owner");
        _;
    }

    modifier onlyPendingProxyOwner() {
        require(msg.sender == pendingProxyOwner(), "only pending Proxy Owner");
        _;
    }

    function proxyOwner() public view returns (address owner) {
        bytes32 position = proxyOwnerPosition;
        assembly {
            owner := sload(position)
        }
    }

    function pendingProxyOwner() public view returns (address pendingOwner) {
        bytes32 position = pendingProxyOwnerPosition;
        assembly {
            pendingOwner := sload(position)
        }
    }

    function _setUpgradeabilityOwner(address newProxyOwner) internal {
        bytes32 position = proxyOwnerPosition;
        assembly {
            sstore(position, newProxyOwner)
        }
    }

    function _setPendingUpgradeabilityOwner(address newPendingProxyOwner) internal {
        bytes32 position = pendingProxyOwnerPosition;
        assembly {
            sstore(position, newPendingProxyOwner)
        }
    }

    function transferProxyOwnership(address newOwner) external onlyProxyOwner {
        require(newOwner != address(0));
        _setPendingUpgradeabilityOwner(newOwner);
        emit NewPendingOwner(proxyOwner(), newOwner);
    }

    function claimProxyOwnership() external onlyPendingProxyOwner {
        emit ProxyOwnershipTransferred(proxyOwner(), pendingProxyOwner());
        _setUpgradeabilityOwner(pendingProxyOwner());
        _setPendingUpgradeabilityOwner(address(0));
    }

    function upgradeTo(address implementation) external onlyProxyOwner {
        _upgradeTo(implementation);
    }
}

contract TokenController {
    using SafeMath for uint256;

    struct MintOperation {
        address to;
        uint256 value;
        uint256 requestedBlock;
        uint256 numberOfApproval;
        bool paused;
        mapping(address => bool) approved;
    }

    address public owner;
    address public pendingOwner;

    bool public initialized;

    uint256 public instantMintThreshold;
    uint256 public ratifiedMintThreshold;
    uint256 public multiSigMintThreshold;

    uint256 public instantMintLimit;
    uint256 public ratifiedMintLimit;
    uint256 public multiSigMintLimit;

    uint256 public instantMintPool;
    uint256 public ratifiedMintPool;
    uint256 public multiSigMintPool;
    address[2] public ratifiedPoolRefillApprovals;

    uint8 constant public RATIFY_MINT_SIGS = 1;
    uint8 constant public MULTISIG_MINT_SIGS = 3;

    bool public mintPaused;
    uint256 public mintReqInvalidBeforeThisBlock;
    address public mintKey;
    MintOperation[] public mintOperations;

    TrueUSD public trueUSD;
    Registry public registry;
    address public trueUsdFastPause;

    bytes32 constant public IS_MINT_PAUSER = "isTUSDMintPausers";
    bytes32 constant public IS_MINT_RATIFIER = "isTUSDMintRatifier";
    bytes32 constant public IS_REDEMPTION_ADMIN = "isTUSDRedemptionAdmin";

    modifier onlyFastPauseOrOwner() {
        require(msg.sender == trueUsdFastPause || msg.sender == owner, "must be pauser or owner");
        _;
    }

    modifier onlyMintKeyOrOwner() {
        require(msg.sender == mintKey || msg.sender == owner, "must be mintKey or owner");
        _;
    }

    modifier onlyMintPauserOrOwner() {
        require(registry.hasAttribute(msg.sender, IS_MINT_PAUSER) || msg.sender == owner, "must be pauser or owner");
        _;
    }

    modifier onlyMintRatifierOrOwner() {
        require(registry.hasAttribute(msg.sender, IS_MINT_RATIFIER) || msg.sender == owner, "must be ratifier or owner");
        _;
    }

    modifier onlyOwnerOrRedemptionAdmin() {
        require(registry.hasAttribute(msg.sender, IS_REDEMPTION_ADMIN) || msg.sender == owner, "must be Redemption admin or owner");
        _;
    }

    modifier mintNotPaused() {
        if (msg.sender != owner) {
            require(!mintPaused, "minting is paused");
        }
        _;
    }
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event NewOwnerPending(address indexed currentOwner, address indexed pendingOwner);
    event SetRegistry(address indexed registry);
    event TransferChild(address indexed child, address indexed newOwner);
    event RequestReclaimContract(address indexed other);
    event SetTrueUSD(TrueUSD newContract);
    event TrueUsdInitialized();

    event RequestMint(address indexed to, uint256 indexed value, uint256 opIndex, address mintKey);
    event FinalizeMint(address indexed to, uint256 indexed value, uint256 opIndex, address mintKey);
    event InstantMint(address indexed to, uint256 indexed value, address indexed mintKey);

    event TransferMintKey(address indexed previousMintKey, address indexed newMintKey);
    event MintRatified(uint256 indexed opIndex, address indexed ratifier);
    event RevokeMint(uint256 opIndex);
    event AllMintsPaused(bool status);
    event MintPaused(uint opIndex, bool status);
    event MintApproved(address approver, uint opIndex);
    event TrueUsdFastPauseSet(address _newFastPause);

    event MintThresholdChanged(uint instant, uint ratified, uint multiSig);
    event MintLimitsChanged(uint instant, uint ratified, uint multiSig);
    event InstantPoolRefilled();
    event RatifyPoolRefilled();
    event MultiSigPoolRefilled();

    function initialize() external {
        require(!initialized, "already initialized");
        owner = msg.sender;
        initialized = true;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "only Owner");
        _;
    }

    modifier onlyPendingOwner() {
        require(msg.sender == pendingOwner);
        _;
    }

    function transferOwnership(address newOwner) external onlyOwner {
        pendingOwner = newOwner;
        emit NewOwnerPending(owner, pendingOwner);
    }

    function claimOwnership() external onlyPendingOwner {
        emit OwnershipTransferred(owner, pendingOwner);
        owner = pendingOwner;
        pendingOwner = address(0);
    }

    function transferTusdProxyOwnership(address _newOwner) external onlyOwner {
        OwnedUpgradeabilityProxy(trueUSD).transferProxyOwnership(_newOwner);
    }

    function claimTusdProxyOwnership() external onlyOwner {
        OwnedUpgradeabilityProxy(trueUSD).claimProxyOwnership();
    }

    function upgradeTusdProxyImplTo(address _implementation) external onlyOwner {
        OwnedUpgradeabilityProxy(trueUSD).upgradeTo(_implementation);
    }

    function setMintThresholds(uint256 _instant, uint256 _ratified, uint256 _multiSig) external onlyOwner {
        require(_instant < _ratified && _ratified < _multiSig);
        instantMintThreshold = _instant;
        ratifiedMintThreshold = _ratified;
        multiSigMintThreshold = _multiSig;
        emit MintThresholdChanged(_instant, _ratified, _multiSig);
    }

    function setMintLimits(uint256 _instant, uint256 _ratified, uint256 _multiSig) external onlyOwner {
        require(_instant < _ratified && _ratified < _multiSig);
        instantMintLimit = _instant;
        ratifiedMintLimit = _ratified;
        multiSigMintLimit = _multiSig;
        emit MintLimitsChanged(_instant, _ratified, _multiSig);
    }

    function refillInstantMintPool() external onlyMintRatifierOrOwner {
        ratifiedMintPool = ratifiedMintPool.sub(instantMintLimit.sub(instantMintPool));
        instantMintPool = instantMintLimit;
        emit InstantPoolRefilled();
    }

    function refillRatifiedMintPool() external onlyMintRatifierOrOwner {
        if (msg.sender != owner) {
            address[2] memory refillApprovals = ratifiedPoolRefillApprovals;
            require(msg.sender != refillApprovals[0] && msg.sender != refillApprovals[1]);
            if (refillApprovals[0] == address(0)) {
                ratifiedPoolRefillApprovals[0] = msg.sender;
                return;
            }
            if (refillApprovals[1] == address(0)) {
                ratifiedPoolRefillApprovals[1] = msg.sender;
                return;
            }
        }
        delete ratifiedPoolRefillApprovals;
        multiSigMintPool = multiSigMintPool.sub(ratifiedMintLimit.sub(ratifiedMintPool));
        ratifiedMintPool = ratifiedMintLimit;
        emit RatifyPoolRefilled();
    }

    function refillMultiSigMintPool() external onlyOwner {
        multiSigMintPool = multiSigMintLimit;
        emit MultiSigPoolRefilled();
    }

    function requestMint(address _to, uint256 _value) external mintNotPaused onlyMintKeyOrOwner {
        MintOperation memory op = MintOperation(_to, _value, block.number, 0, false);
        emit RequestMint(_to, _value, mintOperations.length, msg.sender);
        mintOperations.push(op);
    }

    function instantMint(address _to, uint256 _value) external mintNotPaused onlyMintKeyOrOwner {
        require(_value <= instantMintThreshold, "over the instant mint threshold");
        require(_value <= instantMintPool, "instant mint pool is dry");
        instantMintPool = instantMintPool.sub(_value);
        emit InstantMint(_to, _value, msg.sender);
        trueUSD.mint(_to, _value);
    }

    function ratifyMint(uint256 _index, address _to, uint256 _value) external mintNotPaused onlyMintRatifierOrOwner {
        MintOperation memory op = mintOperations[_index];
        require(op.to == _to, "to address does not match");
        require(op.value == _value, "amount does not match");
        require(!mintOperations[_index].approved[msg.sender], "already approved");
        mintOperations[_index].approved[msg.sender] = true;
        mintOperations[_index].numberOfApproval = mintOperations[_index].numberOfApproval.add(1);
        emit MintRatified(_index, msg.sender);
        if (hasEnoughApproval(mintOperations[_index].numberOfApproval, _value)){
            finalizeMint(_index);
        }
    }

    function finalizeMint(uint256 _index) public mintNotPaused {
        MintOperation memory op = mintOperations[_index];
        address to = op.to;
        uint256 value = op.value;
        if (msg.sender != owner) {
            require(canFinalize(_index));
            _subtractFromMintPool(value);
        }
        delete mintOperations[_index];
        trueUSD.mint(to, value);
        emit FinalizeMint(to, value, _index, msg.sender);
    }

    function _subtractFromMintPool(uint256 _value) internal {
        if (_value <= ratifiedMintPool && _value <= ratifiedMintThreshold) {
            ratifiedMintPool = ratifiedMintPool.sub(_value);
        } else {
            multiSigMintPool = multiSigMintPool.sub(_value);
        }
    }

    function hasEnoughApproval(uint256 _numberOfApproval, uint256 _value) public view returns (bool) {
        if (_value <= ratifiedMintPool && _value <= ratifiedMintThreshold) {
            if (_numberOfApproval >= RATIFY_MINT_SIGS){
                return true;
            }
        }
        if (_value <= multiSigMintPool && _value <= multiSigMintThreshold) {
            if (_numberOfApproval >= MULTISIG_MINT_SIGS){
                return true;
            }
        }
        if (msg.sender == owner) {
            return true;
        }
        return false;
    }

    function canFinalize(uint256 _index) public view returns(bool) {
        MintOperation memory op = mintOperations[_index];
        require(op.requestedBlock > mintReqInvalidBeforeThisBlock, "this mint is invalid");
        require(!op.paused, "this mint is paused");
        require(hasEnoughApproval(op.numberOfApproval, op.value), "not enough approvals");
        return true;
    }

    function revokeMint(uint256 _index) external onlyMintKeyOrOwner {
        delete mintOperations[_index];
        emit RevokeMint(_index);
    }

    function mintOperationCount() public view returns (uint256) {
        return mintOperations.length;
    }

    function transferMintKey(address _newMintKey) external onlyOwner {
        require(_newMintKey != address(0), "new mint key cannot be 0x0");
        emit TransferMintKey(mintKey, _newMintKey);
        mintKey = _newMintKey;
    }

    function invalidateAllPendingMints() external onlyOwner {
        mintReqInvalidBeforeThisBlock = block.number;
    }

    function pauseMints() external onlyMintPauserOrOwner {
        mintPaused = true;
        emit AllMintsPaused(true);
    }

    function unpauseMints() external onlyOwner {
        mintPaused = false;
        emit AllMintsPaused(false);
    }

    function pauseMint(uint _opIndex) external onlyMintPauserOrOwner {
        mintOperations[_opIndex].paused = true;
        emit MintPaused(_opIndex, true);
    }

    function unpauseMint(uint _opIndex) external onlyOwner {
        mintOperations[_opIndex].paused = false;
        emit MintPaused(_opIndex, false);
    }

    function incrementRedemptionAddressCount() external onlyOwnerOrRedemptionAdmin {
        trueUSD.incrementRedemptionAddressCount();
    }

    function setTrueUSD(TrueUSD _newContract) external onlyOwner {
        trueUSD = _newContract;
        emit SetTrueUSD(_newContract);
    }

    function initializeTrueUSD(uint256 _totalSupply) external onlyOwner {
        trueUSD.initialize(_totalSupply);
        emit TrueUsdInitialized();
    }

    function setRegistry(Registry _registry) external onlyOwner {
        registry = _registry;
        emit SetRegistry(registry);
    }

    function changeTokenName(string _name, string _symbol) external onlyOwner {
        trueUSD.changeTokenName(_name, _symbol);
    }

    function setTusdRegistry(Registry _registry) external onlyOwner {
        trueUSD.setRegistry(_registry);
    }

    function issueClaimOwnership(address _other) public onlyOwner {
        HasOwner other = HasOwner(_other);
        other.claimOwnership();
    }

    function claimStorageForProxy(
        TrueUSD _proxy,
        HasOwner _balanceSheet,
        HasOwner _allowanceSheet) external onlyOwner {

        _proxy.setBalanceSheet(_balanceSheet);
        _proxy.setAllowanceSheet(_allowanceSheet);
    }

    function transferChild(HasOwner _child, address _newOwner) external onlyOwner {
        _child.transferOwnership(_newOwner);
        emit TransferChild(_child, _newOwner);
    }

    function requestReclaimContract(Ownable _other) public onlyOwner {
        trueUSD.reclaimContract(_other);
        emit RequestReclaimContract(_other);
    }

    function requestReclaimEther() external onlyOwner {
        trueUSD.reclaimEther(owner);
    }

    function requestReclaimToken(ERC20 _token) external onlyOwner {
        trueUSD.reclaimToken(_token, owner);
    }

    function setGlobalPause(address _newGlobalPause) external onlyOwner {
        trueUSD.setGlobalPause(_newGlobalPause);
    }

    function setTrueUsdFastPause(address _newFastPause) external onlyOwner {
        trueUsdFastPause = _newFastPause;
        emit TrueUsdFastPauseSet(_newFastPause);
    }

    function pauseTrueUSD() external onlyFastPauseOrOwner {
        trueUSD.pause();
    }

    function unpauseTrueUSD() external onlyOwner {
        trueUSD.unpause();
    }

    function wipeBlackListedTrueUSD(address _blacklistedAddress) external onlyOwner {
        trueUSD.wipeBlacklistedAccount(_blacklistedAddress);
    }

    function setBurnBounds(uint256 _min, uint256 _max) external onlyOwner {
        trueUSD.setBurnBounds(_min, _max);
    }

    function reclaimEther(address _to) external onlyOwner {
        _to.transfer(address(this).balance);
    }

    function reclaimToken(ERC20 _token, address _to) external onlyOwner {
        uint256 balance = _token.balanceOf(this);
        _token.transfer(_to, balance);
    }
}
