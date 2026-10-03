pragma solidity ^0.4.24;

contract Ownable {
  address private _owner;

  event OwnershipTransferred(
    address indexed previousOwner,
    address indexed newOwner
  );

  constructor() internal {
    _owner = msg.sender;
    emit OwnershipTransferred(address(0), _owner);
  }

  function owner() public view returns(address) {
    return _owner;
  }

  modifier onlyOwner() {
    require(isOwner());
    _;
  }

  function isOwner() public view returns(bool) {
    return msg.sender == _owner;
  }

  function renounceOwnership() public onlyOwner {
    emit OwnershipTransferred(_owner, address(0));
    _owner = address(0);
  }

  function transferOwnership(address newOwner) public onlyOwner {
    _transferOwnership(newOwner);
  }

  function _transferOwnership(address newOwner) internal {
    require(newOwner != address(0));
    emit OwnershipTransferred(_owner, newOwner);
    _owner = newOwner;
  }
}

interface IERC20 {
  function totalSupply() external view returns (uint256);

  function balanceOf(address who) external view returns (uint256);

  function allowance(address owner, address spender)
    external view returns (uint256);

  function transfer(address to, uint256 value) external returns (bool);

  function approve(address spender, uint256 value)
    external returns (bool);

  function transferFrom(address from, address to, uint256 value)
    external returns (bool);

  event Transfer(
    address indexed from,
    address indexed to,
    uint256 value
  );

  event Approval(
    address indexed owner,
    address indexed spender,
    uint256 value
  );
}

contract ERC20Detailed is IERC20 {
  string private _name;
  string private _symbol;
  uint8 private _decimals;

  constructor(string name, string symbol, uint8 decimals) public {
    _name = name;
    _symbol = symbol;
    _decimals = decimals;
  }

  function name() public view returns(string) {
    return _name;
  }

  function symbol() public view returns(string) {
    return _symbol;
  }

  function decimals() public view returns(uint8) {
    return _decimals;
  }
}

contract SupportEscrow is Ownable {
    ERC20Detailed public constant bznToken = ERC20Detailed(0x1BD223e638aEb3A943b8F617335E04f3e6B6fFfa);
    ERC20Detailed public constant gusdToken = ERC20Detailed(0x056Fd409E1d7A124BD7017459dFEa2F387b6d5Cd);

    uint256 public constant bznRequirement = 13213 * (10 ** uint256(18));

    uint256 public constant gusdRequirement = 330330;

    uint256 public constant gusdMinimum = 33033;

    uint256 public constant unlockDate = 1551330000;

    bool public redeemed = false;
    bool public executed = false;
    bool public redeemable = false;
    address public thirdParty;

    modifier onlyThridParty {
        require(msg.sender == thirdParty);
        _;
    }

    constructor(address tp) public {
        thirdParty = tp;
    }

    function validate() public view returns (bool) {
        address self = address(this);

        uint256 bzn = bznToken.balanceOf(self);
        uint256 gusd = gusdToken.balanceOf(self);

        return bzn >= bznRequirement && gusd >= gusdRequirement;
    }

    function execute() public onlyOwner returns (bool) {

        require(executed == false);

        address self = address(this);
        uint256 bzn = bznToken.balanceOf(self);

        require(bzn >= bznRequirement);

        bznToken.transfer(owner(), bznRequirement);

        executed = true;
    }

    function destroy() public onlyOwner {
        address self = address(this);

        uint256 bzn = bznToken.balanceOf(self);
        uint256 gusd = gusdToken.balanceOf(self);

        if (executed == false) {

            bznToken.transfer(thirdParty, bzn);
            bznToken.transfer(thirdParty, gusd);
        } else if (redeemable && redeemed == false) {

            bznToken.transfer(thirdParty, bzn);
            bznToken.transfer(thirdParty, gusd);
        }

        selfdestruct(owner());
    }

    function allowRedeem() public onlyThridParty returns (uint256) {

        require(executed);

        require(redeemed == false);

        require(redeemable == false);

        require(block.timestamp >= unlockDate);

        require(validate());

        redeemable = true;
    }

    function redeem() public onlyOwner returns (uint256) {

        require(executed);

        require(redeemable);

        require(redeemed == false);

        require(block.timestamp >= unlockDate);

        require(validate());

        bznToken.transfer(thirdParty, bznRequirement);

        gusdToken.transfer(owner(), gusdRequirement);

        redeemed = true;
    }

    function withdrawBZN(uint256 amount) public onlyThridParty {

        require(executed == false);

        bznToken.transfer(thirdParty, amount);
    }
}
