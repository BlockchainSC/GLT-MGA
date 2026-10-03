pragma solidity ^0.4.24;

contract Migratable {

  event Migrated(string contractName, string migrationId);

  mapping (string => mapping (string => bool)) internal migrated;

  string constant private INITIALIZED_ID = "initialized";

  modifier isInitializer(string contractName, string migrationId) {
    validateMigrationIsPending(contractName, INITIALIZED_ID);
    validateMigrationIsPending(contractName, migrationId);
    _;
    emit Migrated(contractName, migrationId);
    migrated[contractName][migrationId] = true;
    migrated[contractName][INITIALIZED_ID] = true;
  }

  modifier isMigration(string contractName, string requiredMigrationId, string newMigrationId) {
    require(isMigrated(contractName, requiredMigrationId), "Prerequisite migration ID has not been run yet");
    validateMigrationIsPending(contractName, newMigrationId);
    _;
    emit Migrated(contractName, newMigrationId);
    migrated[contractName][newMigrationId] = true;
  }

  function isMigrated(string contractName, string migrationId) public view returns(bool) {
    return migrated[contractName][migrationId];
  }

  function initialize() isInitializer("Migratable", "1.2.1") public {
  }

  function validateMigrationIsPending(string contractName, string migrationId) private view {
    require(!isMigrated(contractName, migrationId), "Requested target migration ID has already been run");
  }
}

contract Ownable is Migratable {
  address public owner;

  event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

  function initialize(address _sender) public isInitializer("Ownable", "1.9.0") {
    owner = _sender;
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

contract Pausable is Migratable, Ownable {
  event Pause();
  event Unpause();

  bool public paused = false;

  function initialize(address _sender) isInitializer("Pausable", "1.9.0")  public {
    Ownable.initialize(_sender);
  }

  modifier whenNotPaused() {
    require(!paused);
    _;
  }

  modifier whenPaused() {
    require(paused);
    _;
  }

  function pause() onlyOwner whenNotPaused public {
    paused = true;
    emit Pause();
  }

  function unpause() onlyOwner whenPaused public {
    paused = false;
    emit Unpause();
  }
}

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

library AddressUtils {

  function isContract(address addr) internal view returns (bool) {
    uint256 size;

    assembly { size := extcodesize(addr) }
    return size > 0;
  }

}

contract ERC20Interface {
  function transferFrom(address from, address to, uint tokens) public returns (bool success);
}

contract ERC721Interface {
  function ownerOf(uint256 _tokenId) public view returns (address _owner);
  function approve(address _to, uint256 _tokenId) public;
  function getApproved(uint256 _tokenId) public view returns (address);
  function isApprovedForAll(address _owner, address _operator) public view returns (bool);
  function safeTransferFrom(address _from, address _to, uint256 _tokenId) public;
  function supportsInterface(bytes4) public view returns (bool);
}

contract ERC721Verifiable is ERC721Interface {
  function verifyFingerprint(uint256, bytes) public view returns (bool);
}

contract MarketplaceStorage {
  ERC20Interface public acceptedToken;

  struct Order {

    bytes32 id;

    address seller;

    address nftAddress;

    uint256 price;

    uint256 expiresAt;
  }

  mapping (address => mapping(uint256 => Order)) public orderByAssetId;

  uint256 public ownerCutPerMillion;
  uint256 public publicationFeeInWei;

  address public legacyNFTAddress;

  bytes4 public constant InterfaceId_ValidateFingerprint = bytes4(
    keccak256("verifyFingerprint(uint256,bytes)")
  );

  bytes4 public constant ERC721_Interface = bytes4(0x80ac58cd);

  event OrderCreated(
    bytes32 id,
    uint256 indexed assetId,
    address indexed seller,
    address nftAddress,
    uint256 priceInWei,
    uint256 expiresAt
  );
  event OrderSuccessful(
    bytes32 id,
    uint256 indexed assetId,
    address indexed seller,
    address nftAddress,
    uint256 totalPrice,
    address indexed buyer
  );
  event OrderCancelled(
    bytes32 id,
    uint256 indexed assetId,
    address indexed seller,
    address nftAddress
  );

  event ChangedPublicationFee(uint256 publicationFee);
  event ChangedOwnerCutPerMillion(uint256 ownerCutPerMillion);
  event ChangeLegacyNFTAddress(address indexed legacyNFTAddress);

  event AuctionCreated(
    bytes32 id,
    uint256 indexed assetId,
    address indexed seller,
    uint256 priceInWei,
    uint256 expiresAt
  );
  event AuctionSuccessful(
    bytes32 id,
    uint256 indexed assetId,
    address indexed seller,
    uint256 totalPrice,
    address indexed winner
  );
  event AuctionCancelled(
    bytes32 id,
    uint256 indexed assetId,
    address indexed seller
  );
}

contract Marketplace is Migratable, Ownable, Pausable, MarketplaceStorage {
  using SafeMath for uint256;
  using AddressUtils for address;

  function setPublicationFee(uint256 _publicationFee) external onlyOwner {
    publicationFeeInWei = _publicationFee;
    emit ChangedPublicationFee(publicationFeeInWei);
  }

  function setOwnerCutPerMillion(uint256 _ownerCutPerMillion) external onlyOwner {
    require(_ownerCutPerMillion < 1000000, "The owner cut should be between 0 and 999,999");

    ownerCutPerMillion = _ownerCutPerMillion;
    emit ChangedOwnerCutPerMillion(ownerCutPerMillion);
  }

  function setLegacyNFTAddress(address _legacyNFTAddress) external onlyOwner {
    _requireERC721(_legacyNFTAddress);

    legacyNFTAddress = _legacyNFTAddress;
    emit ChangeLegacyNFTAddress(legacyNFTAddress);
  }

  function initialize(
    address _acceptedToken,
    address _legacyNFTAddress,
    address _owner
  )
    public
    isInitializer("Marketplace", "0.0.1")
  {

    require(_owner != address(0), "Invalid owner");
    Pausable.initialize(_owner);

    require(_acceptedToken.isContract(), "The accepted token address must be a deployed contract");
    acceptedToken = ERC20Interface(_acceptedToken);

    _requireERC721(_legacyNFTAddress);
    legacyNFTAddress = _legacyNFTAddress;
  }

  function createOrder(
    address nftAddress,
    uint256 assetId,
    uint256 priceInWei,
    uint256 expiresAt
  )
    public
    whenNotPaused
  {
    _createOrder(
      nftAddress,
      assetId,
      priceInWei,
      expiresAt
    );
  }

  function createOrder(
    uint256 assetId,
    uint256 priceInWei,
    uint256 expiresAt
  )
    public
    whenNotPaused
  {
    _createOrder(
      legacyNFTAddress,
      assetId,
      priceInWei,
      expiresAt
    );

    Order memory order = orderByAssetId[legacyNFTAddress][assetId];
    emit AuctionCreated(
      order.id,
      assetId,
      order.seller,
      order.price,
      order.expiresAt
    );
  }

  function cancelOrder(address nftAddress, uint256 assetId) public whenNotPaused {
    _cancelOrder(nftAddress, assetId);
  }

  function cancelOrder(uint256 assetId) public whenNotPaused {
    Order memory order = _cancelOrder(legacyNFTAddress, assetId);

    emit AuctionCancelled(
      order.id,
      assetId,
      order.seller
    );
  }

  function safeExecuteOrder(
    address nftAddress,
    uint256 assetId,
    uint256 price,
    bytes fingerprint
  )
   public
   whenNotPaused
  {
    _executeOrder(
      nftAddress,
      assetId,
      price,
      fingerprint
    );
  }

  function executeOrder(
    address nftAddress,
    uint256 assetId,
    uint256 price
  )
   public
   whenNotPaused
  {
    _executeOrder(
      nftAddress,
      assetId,
      price,
      ""
    );
  }

  function executeOrder(
    uint256 assetId,
    uint256 price
  )
   public
   whenNotPaused
  {
    Order memory order = _executeOrder(
      legacyNFTAddress,
      assetId,
      price,
      ""
    );

    emit AuctionSuccessful(
      order.id,
      assetId,
      order.seller,
      price,
      msg.sender
    );
  }

  function auctionByAssetId(
    uint256 assetId
  )
    public
    view
    returns
    (bytes32, address, uint256, uint256)
  {
    Order memory order = orderByAssetId[legacyNFTAddress][assetId];
    return (order.id, order.seller, order.price, order.expiresAt);
  }

  function _createOrder(
    address nftAddress,
    uint256 assetId,
    uint256 priceInWei,
    uint256 expiresAt
  )
    internal
  {
    _requireERC721(nftAddress);

    ERC721Interface nftRegistry = ERC721Interface(nftAddress);
    address assetOwner = nftRegistry.ownerOf(assetId);

    require(msg.sender == assetOwner, "Only the owner can create orders");
    require(
      nftRegistry.getApproved(assetId) == address(this) || nftRegistry.isApprovedForAll(assetOwner, address(this)),
      "The contract is not authorized to manage the asset"
    );
    require(priceInWei > 0, "Price should be bigger than 0");
    require(expiresAt > block.timestamp.add(1 minutes), "Publication should be more than 1 minute in the future");

    bytes32 orderId = keccak256(
      abi.encodePacked(
        block.timestamp,
        assetOwner,
        assetId,
        nftAddress,
        priceInWei
      )
    );

    orderByAssetId[nftAddress][assetId] = Order({
      id: orderId,
      seller: assetOwner,
      nftAddress: nftAddress,
      price: priceInWei,
      expiresAt: expiresAt
    });

    if (publicationFeeInWei > 0) {
      require(
        acceptedToken.transferFrom(msg.sender, owner, publicationFeeInWei),
        "Transfering the publication fee to the Marketplace owner failed"
      );
    }

    emit OrderCreated(
      orderId,
      assetId,
      assetOwner,
      nftAddress,
      priceInWei,
      expiresAt
    );
  }

  function _cancelOrder(address nftAddress, uint256 assetId) internal returns (Order) {
    Order memory order = orderByAssetId[nftAddress][assetId];

    require(order.id != 0, "Asset not published");
    require(order.seller == msg.sender || msg.sender == owner, "Unauthorized user");

    bytes32 orderId = order.id;
    address orderSeller = order.seller;
    address orderNftAddress = order.nftAddress;
    delete orderByAssetId[nftAddress][assetId];

    emit OrderCancelled(
      orderId,
      assetId,
      orderSeller,
      orderNftAddress
    );

    return order;
  }

  function _executeOrder(
    address nftAddress,
    uint256 assetId,
    uint256 price,
    bytes fingerprint
  )
   internal returns (Order)
  {
    _requireERC721(nftAddress);

    ERC721Verifiable nftRegistry = ERC721Verifiable(nftAddress);

    if (nftRegistry.supportsInterface(InterfaceId_ValidateFingerprint)) {
      require(
        nftRegistry.verifyFingerprint(assetId, fingerprint),
        "The asset fingerprint is not valid"
      );
    }
    Order memory order = orderByAssetId[nftAddress][assetId];

    require(order.id != 0, "Asset not published");

    address seller = order.seller;

    require(seller != address(0), "Invalid address");
    require(seller != msg.sender, "Unauthorized user");
    require(order.price == price, "The price is not correct");
    require(block.timestamp < order.expiresAt, "The order expired");
    require(seller == nftRegistry.ownerOf(assetId), "The seller is no longer the owner");

    uint saleShareAmount = 0;

    bytes32 orderId = order.id;
    delete orderByAssetId[nftAddress][assetId];

    if (ownerCutPerMillion > 0) {

      saleShareAmount = price.mul(ownerCutPerMillion).div(1000000);

      require(
        acceptedToken.transferFrom(msg.sender, owner, saleShareAmount),
        "Transfering the cut to the Marketplace owner failed"
      );
    }

    require(
      acceptedToken.transferFrom(msg.sender, seller, price.sub(saleShareAmount)),
      "Transfering the sale amount to the seller failed"
    );

    nftRegistry.safeTransferFrom(
      seller,
      msg.sender,
      assetId
    );

    emit OrderSuccessful(
      orderId,
      assetId,
      seller,
      nftAddress,
      price,
      msg.sender
    );

    return order;
  }

  function _requireERC721(address nftAddress) internal view {
    require(nftAddress.isContract(), "The NFT Address should be a contract");

    ERC721Interface nftRegistry = ERC721Interface(nftAddress);
    require(
      nftRegistry.supportsInterface(ERC721_Interface),
      "The NFT contract has an invalid ERC721 implementation"
    );
  }
}
