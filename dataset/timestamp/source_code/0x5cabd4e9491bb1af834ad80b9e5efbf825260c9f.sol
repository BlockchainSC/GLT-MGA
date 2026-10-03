pragma solidity ^0.4.24;

contract ERC20Token {

    uint256 public totalSupply;

    function balanceOf(address _owner) public view returns (uint256 balance);

    function transfer(address _to, uint256 _value) public returns (bool success);

    function transferFrom(address _from, address _to, uint256 _value) public returns (bool success);

    function approve(address _spender, uint256 _value) public returns (bool success);

    function allowance(address _owner, address _spender) public view returns (uint256 remaining);

    event Transfer(address indexed _from, address indexed _to, uint256 _value);
    event Approval(address indexed _owner, address indexed _spender, uint256 _value);
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
        OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}

library SafeMathLib {

    function mul(uint256 a, uint256 b) internal pure returns (uint256) {
        if (a == 0) {
            return 0;
        }
        uint256 c = a * b;
        assert(c / a == b);
        return c;
    }

    function div(uint256 a, uint256 b) internal pure returns (uint256) {
        assert(b > 0 && a > 0);

        uint256 c = a / b;
        return c;
    }

    function sub(uint256 a, uint256 b) internal pure returns (uint256) {
        assert(b <= a);
        return a - b;
    }

    function add(uint256 a, uint256 b) internal pure returns (uint256) {
        uint256 c = a + b;
        assert(c >= a && c >= b);
        return c;
    }
}

contract StandardToken is ERC20Token {
    using SafeMathLib for uint;

    mapping(address => uint256) balances;
    mapping(address => mapping(address => uint256)) allowed;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    function transfer(address _to, uint256 _value) public returns (bool success) {
        require(_value > 0 && balances[msg.sender] >= _value);

        balances[msg.sender] = balances[msg.sender].sub(_value);
        balances[_to] = balances[_to].add(_value);
        Transfer(msg.sender, _to, _value);
        return true;
    }

    function transferFrom(address _from, address _to, uint256 _value) public returns (bool success) {
        require(_value > 0 && balances[_from] >= _value);
        require(allowed[_from][msg.sender] >= _value);

        balances[_to] = balances[_to].add(_value);
        balances[_from] = balances[_from].sub(_value);
        allowed[_from][msg.sender] = allowed[_from][msg.sender].sub(_value);
        Transfer(_from, _to, _value);
        return true;
    }

    function balanceOf(address _owner) public constant returns (uint256 balance) {
        return balances[_owner];
    }

    function approve(address _spender, uint256 _value) public returns (bool success) {
        allowed[msg.sender][_spender] = _value;
        Approval(msg.sender, _spender, _value);
        return true;
    }

    function allowance(address _owner, address _spender) public constant returns (uint256 remaining) {
        return allowed[_owner][_spender];
    }
}

contract WOS is StandardToken, Ownable {
    using SafeMathLib for uint256;

    uint256 INTERVAL_TIME = 63072000;
    uint256 public deadlineToFreedTeamPool=1591198931;
    string public name = "WOS";
    string public symbol = "WOS";
    uint256 public decimals = 18;
    uint256 public INITIAL_SUPPLY = (210) * (10 ** 8) * (10 ** 18);

    uint256 wosPoolForSecondStage;

    uint256 wosPoolForThirdStage;

    uint256 wosPoolToTeam;

    uint256 wosPoolToWosSystem;

    event Freed(address indexed owner, uint256 value);

    function WOS(){
        totalSupply = INITIAL_SUPPLY;

        uint256 peerSupply = totalSupply.div(100);

        balances[msg.sender] = peerSupply.mul(30);

        wosPoolForSecondStage = peerSupply.mul(15);

        wosPoolForThirdStage = peerSupply.mul(20);

        wosPoolToTeam = peerSupply.mul(15);

        wosPoolToWosSystem = peerSupply.mul(20);

    }

    function balanceWosPoolForSecondStage() public constant returns (uint256 remaining) {
        return wosPoolForSecondStage;
    }

    function freedWosPoolForSecondStage() onlyOwner returns (bool success) {
        require(wosPoolForSecondStage > 0);
        require(balances[msg.sender].add(wosPoolForSecondStage) >= balances[msg.sender]
            && balances[msg.sender].add(wosPoolForSecondStage) >= wosPoolForSecondStage);

        balances[msg.sender] = balances[msg.sender].add(wosPoolForSecondStage);
        Freed(msg.sender, wosPoolForSecondStage);
        wosPoolForSecondStage = 0;
        return true;
    }

    function balanceWosPoolForThirdStage() public constant returns (uint256 remaining) {
        return wosPoolForThirdStage;
    }

    function freedWosPoolForThirdStage() onlyOwner returns (bool success) {
        require(wosPoolForThirdStage > 0);
        require(balances[msg.sender].add(wosPoolForThirdStage) >= balances[msg.sender]
            && balances[msg.sender].add(wosPoolForThirdStage) >= wosPoolForThirdStage);

        balances[msg.sender] = balances[msg.sender].add(wosPoolForThirdStage);
        Freed(msg.sender, wosPoolForThirdStage);
        wosPoolForThirdStage = 0;
        return true;
    }

    function balanceWosPoolToTeam() public constant returns (uint256 remaining) {
        return wosPoolToTeam;
    }

    function freedWosPoolToTeam() onlyOwner returns (bool success) {
        require(wosPoolToTeam > 0);
        require(balances[msg.sender].add(wosPoolToTeam) >= balances[msg.sender]
            && balances[msg.sender].add(wosPoolToTeam) >= wosPoolToTeam);

        require(block.timestamp >= deadlineToFreedTeamPool);

        balances[msg.sender] = balances[msg.sender].add(wosPoolToTeam);
        Freed(msg.sender, wosPoolToTeam);
        wosPoolToTeam = 0;
        return true;
    }

    function balanceWosPoolToWosSystem() public constant returns (uint256 remaining) {
        return wosPoolToWosSystem;
    }

    function freedWosPoolToWosSystem() onlyOwner returns (bool success) {
        require(wosPoolToWosSystem > 0);
        require(balances[msg.sender].add(wosPoolToWosSystem) >= balances[msg.sender]
            && balances[msg.sender].add(wosPoolToWosSystem) >= wosPoolToWosSystem);

        balances[msg.sender] = balances[msg.sender].add(wosPoolToWosSystem);
        Freed(msg.sender, wosPoolToWosSystem);
        wosPoolToWosSystem = 0;
        return true;
    }

    function() public payable {
        revert();
    }

}
