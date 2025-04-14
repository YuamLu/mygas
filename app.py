from flask import Flask, request, jsonify, abort
import requests
from datetime import datetime, timedelta
import time
import json
from functools import lru_cache
import os
import logging
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('mygas')

# Load environment variables
load_dotenv()

# Configuration
class Config:
    """Application configuration"""
    # API Configuration
    ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
    MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")
    
    # Etherscan V2 API base URL
    ETHERSCAN_API_URL = "https://api.etherscan.io/v2/api"
    
    # Moralis API URL
    MORALIS_API_URL = "https://deep-index.moralis.io/api/v2.2"
    
    # Moralis supported chain names (mapping from our chain IDs to Moralis format)
    MORALIS_CHAINS = {
        "base": "base",
        "optimism": "optimism"
    }
    
    # Caching settings
    PRICE_CACHE_TTL = 3600  # 1 hour in seconds
    API_CACHE_SIZE = 100
    
    # Transaction query settings
    MAX_TX_LIMIT = 100
    HISTORY_DAYS = 90
    
    # Supported chains mapping
    SUPPORTED_CHAINS = {
        "eth": "Ethereum",
        "arbitrum": "Arbitrum",
        "base": "Base",
        "optimism": "Optimism",
        "bsc": "BSC",
        "polygon": "Polygon",
        "zksync": "zkSync",
        "linea": "Linea"
    }
    
    # Chain IDs for API requests
    CHAIN_IDS = {
        "eth": "1",        # Ethereum Mainnet
        "arbitrum": "42161", # Arbitrum One
        "base": "8453",    # Base
        "optimism": "10",   # Optimism
        "bsc": "56",       # Binance Smart Chain
        "polygon": "137",   # Polygon
        "zksync": "324",   # zkSync Era
        "linea": "59144",   # Linea
    }
    
    # Native Token Symbols
    NATIVE_TOKENS = {
        "eth": "ETH",
        "arbitrum": "ETH",
        "base": "ETH",
        "optimism": "ETH",
        "bsc": "BNB",
        "polygon": "MATIC",
        "zksync": "ETH",
        "linea": "ETH"
    }
    
    # Blockchain Explorer URLs
    EXPLORERS = {
        "eth": "https://etherscan.io/tx/",
        "arbitrum": "https://arbiscan.io/tx/",
        "base": "https://basescan.org/tx/",
        "optimism": "https://optimistic.etherscan.io/tx/",
        "bsc": "https://bscscan.com/tx/",
        "polygon": "https://polygonscan.com/tx/",
        "zksync": "https://explorer.zksync.io/tx/",
        "starknet": "https://starkscan.co/tx/",
        "linea": "https://lineascan.build/tx/",
        "zircuit": "https://explorer.zircuit.com/tx/"
    }
    
    # Native token addresses (wrapped versions for price lookup)
    TOKEN_CONTRACTS = {
        "eth": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
        "arbitrum": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH on Arbitrum
        "base": "0x4200000000000000000000000000000000000006",  # WETH on Base
        "optimism": "0x4200000000000000000000000000000000000006",  # WETH on Optimism
        "bsc": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
        "polygon": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",  # WMATIC
        "zksync": "0x5AEa5775959fBC2557Cc8789bC1bf90A239D9a91",  # WETH on zkSync
        "linea": "0xe5D7C2a44FfDDf6b295A15c148167daaAf5Cf34f",  # WETH on Linea
    }

# Initialize Flask application
app = Flask(__name__)

# Global token price cache
class TokenPriceCache:
    """Caches token prices with expiration"""
    def __init__(self, ttl_seconds=Config.PRICE_CACHE_TTL):
        self.cache = {}
        self.last_update = datetime.min
        self.ttl_seconds = ttl_seconds
    
    def is_expired(self):
        """Check if the cache has expired"""
        now = datetime.now()
        return (now - self.last_update).total_seconds() >= self.ttl_seconds
    
    def get(self, chain):
        """Get a token price for a chain"""
        if self.is_expired():
            return None
        return self.cache.get(chain, 0)
    
    def update(self, prices):
        """Update the price cache"""
        self.cache = prices
        self.last_update = datetime.now()
    
    def set(self, chain, price):
        """Set a single token price"""
        self.cache[chain] = price

# Initialize token price cache
price_cache = TokenPriceCache()

# API Client classes
class MoralisClient:
    """Client for interacting with Moralis API"""
    def __init__(self, api_key=Config.MORALIS_API_KEY, api_url=Config.MORALIS_API_URL):
        if not api_key:
            raise ValueError("Moralis API key is required")
        
        self.api_key = api_key
        self.api_url = api_url
        self.headers = {
            "accept": "application/json",
            "X-API-Key": self.api_key
        }
    
    def _make_request(self, endpoint, params=None):
        """Make a request to the Moralis API with error handling"""
        url = f"{self.api_url}/{endpoint}"
        try:
            logger.info(f"Making Moralis API request to {url}")
            response = requests.get(url, headers=self.headers, params=params)
            
            if response.status_code != 200:
                logger.error(f"Moralis HTTP Error: Status code {response.status_code}, Response: {response.text}")
                if response.status_code == 429:
                    logger.warning("Moralis rate limit exceeded")
                return None
            
            try:
                data = response.json()
                return data
            except json.JSONDecodeError as e:
                logger.error(f"Moralis JSON Decode Error: {e}, Response text: {response.text[:200]}...")
                return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Moralis Request Error: {e}")
            return None
    
    @lru_cache(maxsize=Config.API_CACHE_SIZE)
    def get_cached_response(self, cache_key):
        """Cache API responses to reduce API calls"""
        parts = cache_key.split('|')
        if len(parts) < 2:
            return None
        
        endpoint = parts[0]
        params = json.loads(parts[1]) if len(parts) > 1 else {}
        return self._make_request(endpoint, params)
    
    def get_transactions(self, address, chain, from_date=None):
        """Get native transactions for a wallet using Moralis API"""
        if not address or not chain or chain not in Config.MORALIS_CHAINS:
            return []
        
        moralis_chain = Config.MORALIS_CHAINS.get(chain)
        endpoint = f"{address}"
        
        # Set up parameters
        params = {
            "chain": moralis_chain,
            "limit": Config.MAX_TX_LIMIT
        }
        
        # Add from_date if provided (ISO format)
        if from_date:
            params["from_date"] = from_date
            
        cache_key = f"{endpoint}|{json.dumps(params)}"
        response_data = self.get_cached_response(cache_key)
        
        if not response_data:
            logger.warning(f"No Moralis response data for {chain}")
            return []
        
        if isinstance(response_data, list):
            return response_data
        elif isinstance(response_data, dict) and "result" in response_data:
            return response_data.get("result", [])
        else:
            logger.warning(f"Unexpected Moralis response format for {chain}")
            return []

class EtherscanClient:
    """Client for interacting with Etherscan API v2"""
    def __init__(self, api_key=Config.ETHERSCAN_API_KEY, api_url=Config.ETHERSCAN_API_URL):
        if not api_key:
            raise ValueError("Etherscan API key is required")
        
        self.api_key = api_key
        self.api_url = api_url
    
    def _make_request(self, params):
        """Make a request to the Etherscan API with error handling"""
        try:
            logger.info(f"Making API request to {self.api_url}")
            response = requests.get(self.api_url, params=params)
            
            if response.status_code != 200:
                logger.error(f"HTTP Error: Status code {response.status_code}, Response: {response.text}")
                if response.status_code == 429:
                    logger.warning("Rate limit exceeded")
                return None
            
            try:
                data = response.json()
                return data
            except json.JSONDecodeError as e:
                logger.error(f"JSON Decode Error: {e}, Response text: {response.text[:200]}...")
                return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request Error: {e}")
            return None
    
    @lru_cache(maxsize=Config.API_CACHE_SIZE)
    def get_cached_response(self, params_str):
        """Cache API responses to reduce API calls"""
        params = json.loads(params_str)
        return self._make_request(params)
    
    def resolve_ens(self, ens_name):
        """Resolve an ENS name to an Ethereum address using Etherscan API"""
        if not ens_name or not isinstance(ens_name, str) or not ens_name.endswith('.eth'):
            return None
            
        params = {
            "chainid": "1",  # Ethereum mainnet
            "module": "resolver",
            "action": "resolvename",  # Correct action name for ENS resolution
            "name": ens_name,
            "apikey": self.api_key
        }
        
        data = self._make_request(params)
        
        if data and data.get("status") == "1" and data.get("result"):
            return data.get("result")
        
        # Fallback to web3.py or public resolver if available
        logger.warning(f"Could not resolve ENS name with Etherscan: {ens_name}")
        
        # Try using a public ENS resolver API
        try:
            url = f"https://api.ensideas.com/ens/resolve/{ens_name}"
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            if data and data.get("address"):
                return data.get("address")
        except Exception as e:
            logger.error(f"Error resolving ENS with fallback: {e}")
        
        return None
    
    def get_transactions(self, address, chain, from_block=0):
        """Get native transactions for a wallet on a specific chain using Etherscan API"""
        if not address or not chain or chain not in Config.CHAIN_IDS:
            return []
            
        chain_id = Config.CHAIN_IDS.get(chain)
        
        params = {
            "chainid": chain_id,
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": from_block,
            "endblock": 99999999,
            "page": 1,
            "offset": Config.MAX_TX_LIMIT,
            "sort": "desc",
            "apikey": self.api_key
        }
        
        logger.info(f"Fetching transactions for {chain} with params: {params}")
        
        params_str = json.dumps(params)
        response_data = self.get_cached_response(params_str)
        
        if not response_data:
            logger.warning(f"No response data for {chain}")
            return []
        
        logger.info(f"Response status for {chain}: {response_data.get('status')}, message: {response_data.get('message')}")
        
        if response_data.get("status") != "1":
            if response_data and response_data.get("message") == "No transactions found":
                logger.info(f"No transactions found for {address} on {chain}")
                return []
            logger.warning(f"Failed to get transactions for {chain}: {response_data.get('message') if response_data else 'No response'}")
            return []
        
        result = response_data.get("result", [])
        logger.info(f"Found {len(result)} transactions for {address} on {chain}")
        return result
    
    def get_token_price(self, chain):
        """Get current token price for a specific chain"""
        # For Ethereum, we can use the native Etherscan API endpoint for token price
        if chain == "eth":
            params = {
                "chainid": "1",
                "module": "stats",
                "action": "ethprice",
                "apikey": self.api_key
            }
            
            data = self._make_request(params)
            if data and data.get("status") == "1" and data.get("result"):
                return float(data["result"]["ethusd"])
        
        # For other chains, we can try to use token pricing when available
        # or fallback to CoinGecko as a temporary solution
        token_symbols = {
            "eth": "ethereum",
            "arbitrum": "ethereum",  # Uses ETH
            "base": "ethereum",      # Uses ETH
            "optimism": "ethereum",  # Uses ETH
            "bsc": "binancecoin",
            "polygon": "matic-network",
            "zksync": "ethereum",    # Uses ETH
            "linea": "ethereum"      # Uses ETH
        }
        
        if chain in token_symbols:
            symbol = token_symbols[chain]
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies=usd"
            
            try:
                response = requests.get(url)
                response.raise_for_status()
                data = response.json()
                
                if data and symbol in data and "usd" in data[symbol]:
                    return data[symbol]["usd"]
            except Exception as e:
                logger.error(f"Error fetching price from CoinGecko for {chain}: {e}")
        
        # Fallback to cached price or default
        return price_cache.get(chain) or 0

    def get_token_prices(self):
        """Get current token prices for all supported chains"""
        prices = {}
        
        # Get current price for each chain's native token
        for chain in Config.SUPPORTED_CHAINS:
            try:
                price = self.get_token_price(chain)
                if price > 0:
                    prices[chain] = price
                    logger.info(f"Price for {chain}: ${price}")
                else:
                    logger.warning(f"Failed to get price for {chain}")
                    # Use previous price if available, otherwise default to 0
                    prices[chain] = price_cache.get(chain) or 0
                    
            except Exception as e:
                logger.error(f"Error fetching price for {chain}: {e}")
                prices[chain] = price_cache.get(chain) or 0
        
        return prices

# Create Etherscan client
etherscan = EtherscanClient()

# Create API clients
moralis = MoralisClient()

# Utility functions
def refresh_token_prices():
    """Refresh token prices from API if cache is expired"""
    if price_cache.is_expired():
        logger.info("Refreshing token prices...")
        prices = etherscan.get_token_prices()
        price_cache.update(prices)
    return price_cache.cache

def get_native_token_symbol(chain):
    """Get the native token symbol for a chain"""
    return Config.NATIVE_TOKENS.get(chain, "GAS")

def get_explorer_url(chain, tx_hash):
    """Get the blockchain explorer URL for a transaction"""
    base_url = Config.EXPLORERS.get(chain, "")
    if base_url and tx_hash:
        return f"{base_url}{tx_hash}"
    return ""

def is_valid_ethereum_address(address):
    """Simple validation for Ethereum addresses"""
    if not address or not isinstance(address, str):
        return False
    return address.startswith('0x') and len(address) == 42

def validate_address_param(address):
    """Validate and process address parameter"""
    if not address:
        return None, "Address is required"
    
    # Check if address is ENS name
    if address.lower().endswith('.eth'):
        resolved_address = etherscan.resolve_ens(address)
        if not resolved_address:
            return None, f"Could not resolve ENS name: {address}"
        address = resolved_address
    
    # Validate Ethereum address
    if not is_valid_ethereum_address(address):
        return None, f"Invalid Ethereum address: {address}"
    
    return address, None

def get_from_block(days=Config.HISTORY_DAYS):
    """Estimate a starting block number based on days (rough approximation)"""
    # Ethereum averages ~6500 blocks per day
    # This is a simplified approach - you might want to use a more accurate method
    return 0  # Default to 0 to get all transactions, then filter by date later

def get_from_date(days=Config.HISTORY_DAYS):
    """Get ISO formatted date string for 'days' ago"""
    cutoff_date = datetime.now() - timedelta(days=days)
    return cutoff_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def process_moralis_transaction(tx, cutoff_date):
    """Process a single Moralis transaction and extract relevant data"""
    tx_hash = tx.get("hash", "")
    
    # Parse timestamp
    timestamp_str = tx.get("block_timestamp")
    if not timestamp_str:
        raise ValueError(f"Missing timestamp in transaction: {tx_hash}")
        
    # Parse ISO 8601 timestamp
    tx_date = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S.%fZ")
    
    # Skip transactions older than cutoff date
    if tx_date < cutoff_date:
        raise ValueError("Transaction too old")
    
    # Get gas data
    gas_used = int(tx.get("receipt_gas_used", "0"))
    if gas_used == 0:
        gas_used = int(tx.get("gas", "0"))  # Fall back to gas limit if gas_used not available
    
    # Get gas cost in ETH
    if tx.get("transaction_fee"):
        # Use transaction_fee directly from Moralis
        gas_cost_eth = float(tx.get("transaction_fee"))
    else:
        # Fallback calculation
        gas_price = int(tx.get("gas_price", "0")) / 1e18  # Convert Wei to ETH
        gas_cost_eth = gas_price * gas_used
    
    return tx_hash, tx_date, gas_used, gas_cost_eth

def process_etherscan_transaction(tx, cutoff_date):
    """Process a single Etherscan transaction and extract relevant data"""
    tx_hash = tx.get("hash", "")
    
    # Parse timestamp (Unix timestamp in seconds)
    timestamp = int(tx.get("timeStamp", "0"))
    tx_date = datetime.fromtimestamp(timestamp)
    
    # Handle future timestamps in test data
    if tx_date > datetime.now():
        logger.debug(f"Future timestamp detected in transaction {tx_hash}: {timestamp}")
        tx_date = datetime.now() - timedelta(days=7)  # Set to 7 days ago
    
    # Skip transactions older than cutoff date
    if tx_date < cutoff_date:
        raise ValueError("Transaction too old")
    
    # Calculate gas cost
    gas_price = int(tx.get("gasPrice", "0")) / 1e18  # Convert Wei to ETH
    gas_used = int(tx.get("gasUsed", "0"))
    gas_cost_eth = gas_price * gas_used
    
    return tx_hash, tx_date, gas_used, gas_cost_eth

def process_transactions(transactions, chain, token_prices, is_moralis=False):
    """Process transactions to extract gas data"""
    result = []
    
    # Handle empty transaction list
    if not transactions:
        logger.info(f"No transactions to process for {chain}")
        return result
    
    # Get the current token price for this chain
    token_price = token_prices.get(chain, 0)
    logger.info(f"Using token price for {chain}: {token_price}")
    
    # Get current time to filter transactions from the last 3 months
    cutoff_date = datetime.now() - timedelta(days=Config.HISTORY_DAYS)
    
    processed_count = 0
    skipped_count = 0
    error_count = 0
    
    # Select the appropriate transaction processor based on data source
    process_tx = process_moralis_transaction if is_moralis else process_etherscan_transaction
    
    for tx in transactions:
        try:
            # Process transaction data
            tx_hash, tx_date, gas_used, gas_cost_eth = process_tx(tx, cutoff_date)
            
            # Format data for display
            token_symbol = get_native_token_symbol(chain)
            is_eth_based = token_symbol == "ETH"
            gas_cost_gwei = gas_cost_eth * 1e9 if is_eth_based else gas_cost_eth
            
            # Calculate USD cost using token price
            usd_cost = gas_cost_eth * token_price
            
            # Get token display name and explorer URL
            token_display = "Gwei" if is_eth_based else token_symbol
            explorer_url = get_explorer_url(chain, tx_hash)
            
            # Format transaction data
            transaction_data = {
                "chain": Config.SUPPORTED_CHAINS.get(chain, chain),
                "tx": tx_hash,
                "explorer_url": explorer_url,
                "time": tx_date.strftime("%Y-%m-%d %H:%M"),
                "gas": gas_used,
                "token_amount": round(gas_cost_gwei, 9),
                "token_symbol": token_display,
                "usd": round(usd_cost, 2)
            }
            result.append(transaction_data)
            processed_count += 1
            
        except ValueError as e:
            # Skip transaction intentionally (too old or missing data)
            if str(e) == "Transaction too old":
                skipped_count += 1
            else:
                logger.warning(f"Invalid transaction data: {e}")
                error_count += 1
            continue
        except Exception as e:
            logger.error(f"Error processing transaction: {e}")
            error_count += 1
            continue
    
    logger.info(f"Processed {processed_count} transactions for {chain}, skipped {skipped_count}, errors {error_count}")
    return result

def aggregate_by_chain(transactions):
    """Aggregate gas consumption by chain"""
    result = {}
    for tx in transactions:
        chain = tx["chain"]
        gas = tx["gas"]
        token_amount = tx["token_amount"]
        token_symbol = tx["token_symbol"]
        usd = tx["usd"]
        
        if chain not in result:
            result[chain] = {"gas": 0, "token_amount": 0, "token_symbol": token_symbol, "usd": 0}
        
        result[chain]["gas"] += gas
        result[chain]["token_amount"] += token_amount
        result[chain]["usd"] += usd
    
    # Convert to list format
    return [{"chain": chain, "gas": data["gas"], "token_amount": round(data["token_amount"], 9), "token_symbol": data["token_symbol"], "usd": data["usd"]} for chain, data in result.items()]

def format_daily_gas(transactions):
    """Format daily gas consumption for charting"""
    # Group transactions by date
    daily_gas = {}
    for tx in transactions:
        date = tx["time"].split(" ")[0]  # Extract date part
        gas = tx["gas"]
        token_amount = tx["token_amount"]
        token_symbol = tx["token_symbol"]
        usd = tx["usd"]
        
        if date not in daily_gas:
            daily_gas[date] = {"gas": 0, "token_amount": 0, "token_symbol": token_symbol, "usd": 0}
        
        daily_gas[date]["gas"] += gas
        daily_gas[date]["token_amount"] += token_amount
        daily_gas[date]["usd"] += usd
    
    # Convert to list and sort by date
    result = [{"date": date, "gas": data["gas"], "token_amount": round(data["token_amount"], 9), "token_symbol": data["token_symbol"], "usd": data["usd"]} for date, data in daily_gas.items()]
    result.sort(key=lambda x: x["date"])
    
    return result

# API Routes
@app.route('/api/gas', methods=['GET'])
def get_gas_data():
    """API endpoint to get gas consumption data"""
    try:
        address = request.args.get('address', '')
        logger.info(f"Gas data requested for address: {address}")
        
        # Validate and process address
        validated_address, error = validate_address_param(address)
        if error:
            logger.warning(f"Address validation failed: {error}")
            return jsonify({"error": error}), 400
        
        address = validated_address
        logger.info(f"Validated address: {address}")
        
        # Refresh token prices once for all chains
        token_prices = refresh_token_prices()
        logger.info(f"Token prices: {token_prices}")
        
        all_transactions = []
        transactions_by_chain = {}
        
        # Set up time cutoffs
        from_block = get_from_block(Config.HISTORY_DAYS)
        from_date = get_from_date(Config.HISTORY_DAYS)
        
        # Fetch transactions for each supported chain
        logger.info(f"Fetching transactions for {len(Config.SUPPORTED_CHAINS)} chains")
        for chain_id, chain_name in Config.SUPPORTED_CHAINS.items():
            try:
                logger.info(f"Fetching transactions for {chain_name} (chain_id: {chain_id})")
                
                # Use Moralis API for Base and Optimism chains
                if chain_id in ["base", "optimism"]:
                    logger.info(f"Using Moralis API for {chain_name}")
                    transactions = moralis.get_transactions(address, chain_id, from_date)
                    if transactions:
                        logger.info(f"Processing {len(transactions)} Moralis transactions for {chain_name}")
                        processed_transactions = process_transactions(transactions, chain_id, token_prices, is_moralis=True)
                        if processed_transactions:
                            logger.info(f"Adding {len(processed_transactions)} processed Moralis transactions for {chain_name}")
                            all_transactions.extend(processed_transactions)
                            transactions_by_chain[chain_name] = processed_transactions
                        else:
                            logger.info(f"No processed Moralis transactions for {chain_name}")
                            transactions_by_chain[chain_name] = []
                    else:
                        logger.info(f"No Moralis transactions found for {chain_name}")
                        transactions_by_chain[chain_name] = []
                # Use Etherscan API for other chains
                else:
                    logger.info(f"Using Etherscan API for {chain_name}")
                    transactions = etherscan.get_transactions(address, chain_id, from_block)
                    if transactions:
                        logger.info(f"Processing {len(transactions)} Etherscan transactions for {chain_name}")
                        processed_transactions = process_transactions(transactions, chain_id, token_prices)
                        if processed_transactions:
                            logger.info(f"Adding {len(processed_transactions)} processed Etherscan transactions for {chain_name}")
                            all_transactions.extend(processed_transactions)
                            transactions_by_chain[chain_name] = processed_transactions
                        else:
                            logger.info(f"No processed Etherscan transactions for {chain_name}")
                            transactions_by_chain[chain_name] = []
                    else:
                        logger.info(f"No Etherscan transactions found for {chain_name}")
                        transactions_by_chain[chain_name] = []
            except Exception as e:
                logger.error(f"Error fetching transactions for {chain_name}: {e}", exc_info=True)
                transactions_by_chain[chain_name] = []
                continue
        
        # Add "All Chains" category
        transactions_by_chain["All Chains"] = all_transactions
        
        # Aggregate gas consumption by chain
        logger.info(f"Aggregating gas consumption for {len(all_transactions)} transactions")
        gas_blocks = aggregate_by_chain(all_transactions)
        
        # Format daily gas consumption for charting
        logger.info("Formatting daily gas consumption")
        daily_gas = format_daily_gas(all_transactions)
        
        # Return the response, with fallbacks for empty data
        response = {
            "dailyGas": daily_gas if daily_gas else [],
            "gasBlocks": gas_blocks if gas_blocks else [],
            "transactions": transactions_by_chain if transactions_by_chain else {"All Chains": []}
        }
        
        logger.info(f"Returning response with {len(daily_gas)} daily entries, {len(gas_blocks)} gas blocks, {len(all_transactions)} transactions")
        return jsonify(response)
    except Exception as e:
        logger.error(f"Unexpected error in API: {e}", exc_info=True)
        return jsonify({"error": "An unexpected error occurred"}), 500

@app.route('/')
def index():
    """Serve the HTML file"""
    try:
        with open('index.html', 'r') as file:
            return file.read()
    except FileNotFoundError:
        logger.error("index.html file not found")
        return "Index file not found", 404

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({"error": "Internal server error"}), 500

# Main entry point
if __name__ == '__main__':
    # Check if API keys are set
    missing_keys = []
    if not Config.ETHERSCAN_API_KEY:
        missing_keys.append("ETHERSCAN_API_KEY")
    
    if not Config.MORALIS_API_KEY:
        missing_keys.append("MORALIS_API_KEY")
    
    if missing_keys:
        logger.error(f"Missing required environment variables: {', '.join(missing_keys)}")
        print(f"Error: Missing required environment variables: {', '.join(missing_keys)}")
        exit(1)
        
    app.run(port=5001, debug=True) 