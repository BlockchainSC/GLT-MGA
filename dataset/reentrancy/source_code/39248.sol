pragma solidity ^0.4.11;

 contract ContractReceiver {
    function tokenFallback(address _from, uint256 _value, bytes _data){
      _from = _from;
      _value = _value;
      _data = _data;

    }
}

contract ERC23 {
  uint256 public totalSupply;
  function balanceOf(address who) constant returns (uint256);
  function allowance(address owner, address spender) constant returns (uint256);

  function name() constant returns (string _name);
  function symbol() constant returns (string _symbol);
  function decimals() constant returns (uint8 _decimals);
  function totalSupply() constant returns (uint256 _supply);

  function transfer(address to, uint256 value) returns (bool ok);
  function transfer(address to, uint256 value, bytes data) returns (bool ok);
  function transferFrom(address from, address to, uint256 value) returns (bool ok);
  function approve(address spender, uint256 value) returns (bool ok);
  event Transfer(address indexed from, address indexed to, uint256 value);
  event Transfer(address indexed from, address indexed to, uint256 value, bytes data);
  event Approval(address indexed owner, address indexed spender, uint256 value);
}

contract ERC23Token is ERC23 {

  mapping(address => uint256) balances;
  mapping (address => mapping (address => uint256)) allowed;

  string public name;
  string public symbol;
  uint8 public decimals;
  uint256 public totalSupply;

  function name() constant returns (string _name) {
      return name;
  }

  function symbol() constant returns (string _symbol) {
      return symbol;
  }

  function decimals() constant returns (uint8 _decimals) {
      return decimals;
  }

  function totalSupply() constant returns (uint256 _totalSupply) {
      return totalSupply;
  }

  function transfer(address _to, uint256 _value, bytes _data) returns (bool success) {

    if(isContract(_to)) {
        transferToContract(_to, _value, _data);
    }
    else {
        transferToAddress(_to, _value, _data);
    }
    return true;
  }

  function transfer(address _to, uint256 _value) returns (bool success) {

    bytes memory empty;
    if(isContract(_to)) {
        transferToContract(_to, _value, empty);
    }
    else {
        transferToAddress(_to, _value, empty);
    }
    return true;
  }

  function transferToAddress(address _to, uint256 _value, bytes _data) private returns (bool success) {
    balances[msg.sender] -= _value;
    balances[_to] += _value;
    Transfer(msg.sender, _to, _value);
    Transfer(msg.sender, _to, _value, _data);
    return true;
  }

  function transferToContract(address _to, uint256 _value, bytes _data) private returns (bool success) {
    balances[msg.sender] -= _value;
    balances[_to] += _value;
    ContractReceiver reciever = ContractReceiver(_to);
    reciever.tokenFallback(msg.sender, _value, _data);
    Transfer(msg.sender, _to, _value);
    Transfer(msg.sender, _to, _value, _data);
    return true;
  }

  function isContract(address _addr) private returns (bool is_contract) {
      _addr = _addr;
      uint256 length;
      assembly {

            length := extcodesize(_addr)
        }
        if(length>0) {
            return true;
        }
        else {
            return false;
        }
    }

  function transferFrom(address _from, address _to, uint256 _value) returns (bool success) {
    var _allowance = allowed[_from][msg.sender];

    if(_value > _allowance) {
        throw;
    }

    balances[_to] += _value;
    balances[_from] -= _value;
    allowed[_from][msg.sender] -= _value;
    Transfer(_from, _to, _value);
    return true;
  }

  function balanceOf(address _owner) constant returns (uint256 balance) {
    return balances[_owner];
  }

  function approve(address _spender, uint256 _value) returns (bool success) {
    allowed[msg.sender][_spender] = _value;
    Approval(msg.sender, _spender, _value);
    return true;
  }

  function allowance(address _owner, address _spender) constant returns (uint256 remaining) {
    return allowed[_owner][_spender];
  }
}

contract DASToken is ERC23Token {
    mapping (address => bool) blockedAccounts;
    address public secretaryGeneral;

    function DASToken(
            string _name,
            string _symbol,
            uint8 _decimals,
            uint256 _totalSupply,
            address _initialTokensHolder) {
        secretaryGeneral = msg.sender;
        name = _name;
        symbol = _symbol;
        decimals = _decimals;
        totalSupply = _totalSupply;
        balances[_initialTokensHolder] = _totalSupply;
    }

    modifier onlySecretaryGeneral {
        if (msg.sender != secretaryGeneral) throw;
        _;
    }

    function blockAccount(address _account) onlySecretaryGeneral {
        blockedAccounts[_account] = true;
    }

    function unblockAccount(address _account) onlySecretaryGeneral {
        blockedAccounts[_account] = false;
    }

    function isAccountBlocked(address _account) returns (bool){
        return blockedAccounts[_account];
    }

    function transfer(address _to, uint256 _value, bytes _data) returns (bool success) {
        if (blockedAccounts[msg.sender]) {
            throw;
        }
        return ERC23Token.transfer(_to, _value, _data);
    }

    function transfer(address _to, uint256 _value) returns (bool success) {
        if (blockedAccounts[msg.sender]) {
            throw;
        }
        bytes memory empty;
        return ERC23Token.transfer(_to, _value, empty);
    }

    function transferFrom(address _from, address _to, uint256 _value) returns (bool success) {
        if (blockedAccounts[_from]) {
            throw;
        }
        return ERC23Token.transferFrom(_from, _to, _value);
    }
}

contract ABCToken is ERC23Token {

    function ABCToken(
            string _name,
            string _symbol,
            uint8 _decimals,
            uint256 _totalSupply,
            address _initialTokensHolder) {
        name = _name;
        symbol = _symbol;
        decimals = _decimals;
        totalSupply = _totalSupply;
        balances[_initialTokensHolder] = _totalSupply;
    }
}

contract DAS is ContractReceiver {

    string name = "Decentralized Autonomous State";

    DASToken public dasToken;
    ABCToken public abcToken;

    uint256 public congressMemberThreshold;
    uint256 public minimumQuorum;
    uint256 public debatingPeriod;
    uint256 public marginForMajority;

    Proposal[] public proposals;
    uint256 public proposalsNumber = 0;
    mapping (address => uint32) tokensLocks;

    event ProposalAddedEvent(uint256 proposalID, address beneficiary, uint256 etherAmount, string description);
    event VotedEvent(uint256 proposalID, address voter, bool inSupport, uint256 voterTokens, string justificationText);
    event ProposalTalliedEvent(uint256 proposalID, bool quorum, bool result);
    event ProposalExecutedEvent(uint256 proposalID);
    event RulesChangedEvent(uint256 congressMemberThreshold,
                            uint256 minimumQuorum,
                            uint256 debatingPeriod,
                            uint256 marginForMajority);

    enum ProposalState {Proposed, NoQuorum, Rejected, Passed, Executed}

    struct Proposal {

        address beneficiary;
        uint256 etherAmount;
        string description;
        bytes32 proposalHash;

        ProposalState state;

        uint256 votingDeadline;
        Vote[] votes;
        uint256 votesNumber;
        mapping (address => bool) voted;
    }

    struct Vote {
        address voter;
        bool inSupport;
        uint256 voterTokens;
        string justificationText;
    }

    modifier onlyCongressMembers {
        if (dasToken.balanceOf(msg.sender) < congressMemberThreshold) throw;
        _;
    }

    function DAS(
        uint256 _congressMemberThreshold,
        uint256 _minimumQuorum,
        uint256 _debatingPeriod,
        uint256 _marginForMajority,
        address _congressLeader
    ) payable {

        dasToken = new DASToken('DA$', 'DA$', 18, 1000000000 * (10 ** 18), _congressLeader);
        abcToken = new ABCToken('Alphabit', 'ABC', 18, 210000000 * (10 ** 18), _congressLeader);

        congressMemberThreshold = _congressMemberThreshold;
        minimumQuorum = _minimumQuorum;
        debatingPeriod = _debatingPeriod;
        marginForMajority = _marginForMajority;

        RulesChangedEvent(congressMemberThreshold, minimumQuorum, debatingPeriod, marginForMajority);
    }

    function() payable { }

    function getProposalHash(
        address _beneficiary,
        uint256 _etherAmount,
        bytes _transactionBytecode
    )
        constant
        returns (bytes32)
    {
        return sha3(_beneficiary, _etherAmount, _transactionBytecode);
    }

    function blockTokens(address _voter) internal {
        if (tokensLocks[_voter] + 1 < tokensLocks[_voter]) throw;

        tokensLocks[_voter] += 1;
        if (tokensLocks[_voter] == 1) {
            dasToken.blockAccount(_voter);
        }
    }

    function unblockTokens(address _voter) internal {
        if (tokensLocks[_voter] <= 0) throw;

        tokensLocks[_voter] -= 1;
        if (tokensLocks[_voter] == 0) {
            dasToken.unblockAccount(_voter);
        }
    }

    function createProposal(
        address _beneficiary,
        uint256 _etherAmount,
        string _description,
        bytes _transactionBytecode
    )
        onlyCongressMembers
        returns (uint256 _proposalID)
    {
        _proposalID = proposals.length;
        proposals.length += 1;
        proposalsNumber = _proposalID + 1;

        proposals[_proposalID].beneficiary = _beneficiary;
        proposals[_proposalID].etherAmount = _etherAmount;
        proposals[_proposalID].description = _description;
        proposals[_proposalID].proposalHash = getProposalHash(_beneficiary, _etherAmount, _transactionBytecode);
        proposals[_proposalID].state = ProposalState.Proposed;
        proposals[_proposalID].votingDeadline = now + debatingPeriod * 1 seconds;
        proposals[_proposalID].votesNumber = 0;

        ProposalAddedEvent(_proposalID, _beneficiary, _etherAmount, _description);

        return _proposalID;
    }

    function vote(
        uint256 _proposalID,
        bool _inSupport,
        string _justificationText
    )
        onlyCongressMembers
    {
        Proposal p = proposals[_proposalID];

        if (p.state != ProposalState.Proposed) throw;
        if (p.voted[msg.sender] == true) throw;

        var voterTokens = dasToken.balanceOf(msg.sender);
        blockTokens(msg.sender);

        p.voted[msg.sender] = true;
        p.votes.push(Vote(msg.sender, _inSupport, voterTokens, _justificationText));
        p.votesNumber += 1;

        VotedEvent(_proposalID, msg.sender, _inSupport, voterTokens, _justificationText);
    }

    function finishProposalVoting(uint256 _proposalID) onlyCongressMembers {
        Proposal p = proposals[_proposalID];

        if (now < p.votingDeadline) throw;
        if (p.state != ProposalState.Proposed) throw;

        var _votesNumber = p.votes.length;
        uint256 tokensFor = 0;
        uint256 tokensAgainst = 0;
        for (uint256 i = 0; i < _votesNumber; i++) {
            if (p.votes[i].inSupport) {
                tokensFor += p.votes[i].voterTokens;
            }
            else {
                tokensAgainst += p.votes[i].voterTokens;
            }

            unblockTokens(p.votes[i].voter);
        }

        if ((tokensFor + tokensAgainst) < minimumQuorum) {
            p.state = ProposalState.NoQuorum;
            ProposalTalliedEvent(_proposalID, false, false);
            return;
        }
        if ((tokensFor - tokensAgainst) < marginForMajority) {
            p.state = ProposalState.Rejected;
            ProposalTalliedEvent(_proposalID, true, false);
            return;
        }
        p.state = ProposalState.Passed;
        ProposalTalliedEvent(_proposalID, true, true);
        return;
    }

    function executeProposal(uint256 _proposalID, bytes _transactionBytecode) onlyCongressMembers {
        Proposal p = proposals[_proposalID];

        if (p.state != ProposalState.Passed) throw;

        p.state = ProposalState.Executed;
        if (!p.beneficiary.call.value(p.etherAmount * 1 ether)(_transactionBytecode)) {
            throw;
        }

        ProposalExecutedEvent(_proposalID);
    }
}
