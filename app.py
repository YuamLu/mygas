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
    MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")
    MORALIS_BASE_URL = "https://deep-index.moralis.io/api/v2.2"
    
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
        "eth": "0x1",        # Ethereum Mainnet
        "arbitrum": "0xa4b1", # Arbitrum One
        "base": "0x2105",    # Base
        "optimism": "0xa",   # Optimism
        "bsc": "0x38",       # Binance Smart Chain
        "polygon": "0x89",   # Polygon
        "zksync": "0x144",   # zkSync Era
        "linea": "0xe708",   # Linea
        # Note: StarkNet and Zircuit may not be directly supported by Moralis API
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
    
    # Native token addresses (wrapped versions)
    NATIVE_TOKEN_ADDRESSES = {
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

# API Client class
class MoralisClient:
    """Client for interacting with Moralis API"""
    def __init__(self, api_key=Config.MORALIS_API_KEY, base_url=Config.MORALIS_BASE_URL):
        if not api_key:
            raise ValueError("Moralis API key is required")
        
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key
        }
    
    def _make_request(self, url, params=None):
        """Make a request to the Moralis API with error handling"""
        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error: {e}")
            if response.status_code == 429:
                logger.warning("Rate limit exceeded")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request Error: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"JSON Decode Error: {e}")
            return None
    
    @lru_cache(maxsize=Config.API_CACHE_SIZE)
    def get_cached_response(self, url, params_str):
        """Cache API responses to reduce API calls"""
        params = json.loads(params_str)
        return self._make_request(url, params)
    
    def resolve_ens(self, ens_name):
        """Resolve an ENS name to an Ethereum address"""
        if not ens_name or not isinstance(ens_name, str) or not ens_name.endswith('.eth'):
            return None
            
        url = f"{self.base_url}/resolve/ens/{ens_name}"
        data = self._make_request(url)
        
        if data and 'address' in data:
            return data['address']
        return None
    
    def get_transactions(self, address, chain, from_date=None):
        """Get native transactions for a wallet on a specific chain"""
        if not address or not chain:
            return []
            
        chain_id = Config.CHAIN_IDS.get(chain)
        if not chain_id:
            logger.warning(f"Unsupported chain: {chain}")
            return []
        
        params = {
            "chain": chain_id,
            "limit": Config.MAX_TX_LIMIT
        }
        
        # Add from_date parameter if provided
        if from_date:
            params["from_date"] = from_date.strftime("%Y-%m-%d")
        
        url = f"{self.base_url}/{address}"
        params_str = json.dumps(params)
        
        response_data = self.get_cached_response(url, params_str)
        
        # Handle different response formats
        if not response_data:
            return []
        
        # Check if response is a dictionary with 'result' key (newer API format)
        if isinstance(response_data, dict) and 'result' in response_data:
            return response_data.get('result', [])
        
        # If response is already a list (older API format)
        if isinstance(response_data, list):
            return response_data
        
        # Fallback to empty list if unknown format
        return []
    
    def get_token_prices(self):
        """Get current token prices for all supported chains"""
        prices = {}
        
        # Get current price for each chain's native token
        for chain, token_address in Config.NATIVE_TOKEN_ADDRESSES.items():
            if chain not in Config.CHAIN_IDS:
                continue
                
            try:
                params = {"chain": Config.CHAIN_IDS[chain]}
                url = f"{self.base_url}/erc20/{token_address}/price"
                
                data = self._make_request(url, params)
                if data and "usdPrice" in data:
                    prices[chain] = data["usdPrice"]
                    logger.info(f"Price for {chain}: ${data['usdPrice']}")
                else:
                    logger.warning(f"Failed to get price for {chain}")
                    # Use previous price if available, otherwise default to 0
                    prices[chain] = price_cache.get(chain) or 0
                    
            except Exception as e:
                logger.error(f"Error fetching price for {chain}: {e}")
                prices[chain] = price_cache.get(chain) or 0
        
        return prices

# Create Moralis client
moralis = MoralisClient()

# Utility functions
def refresh_token_prices():
    """Refresh token prices from API if cache is expired"""
    if price_cache.is_expired():
        logger.info("Refreshing token prices...")
        prices = moralis.get_token_prices()
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
        resolved_address = moralis.resolve_ens(address)
        if not resolved_address:
            return None, f"Could not resolve ENS name: {address}"
        address = resolved_address
    
    # Validate Ethereum address
    if not is_valid_ethereum_address(address):
        return None, f"Invalid Ethereum address: {address}"
    
    return address, None

# Core business logic functions
def process_transactions(transactions, chain, token_prices):
    """Process transactions to extract gas data"""
    result = []
    
    # Handle empty transaction list
    if not transactions:
        return result
    
    # Get the current token price for this chain
    token_price = token_prices.get(chain, 0)
        
    for tx in transactions:
        try:
            # Extract transaction data
            tx_hash = tx.get("hash", "")
            block_timestamp = tx.get("block_timestamp", "")
            
            # Use the transaction_fee field directly if available
            if "transaction_fee" in tx:
                gas_cost_eth = float(tx.get("transaction_fee", "0"))
            else:
                # Fallback to calculation if transaction_fee is not available
                try:
                    gas_price = int(tx.get("gas_price", "0"), 16) / 1e18 if "gas_price" in tx else 0
                except (ValueError, TypeError):
                    gas_price = 0
                    
                try:
                    gas_used = int(tx.get("receipt_gas_used", "0"), 16) if "receipt_gas_used" in tx else 0
                except (ValueError, TypeError):
                    gas_used = 0
                
                # Calculate gas cost in ETH
                gas_cost_eth = gas_price * gas_used
            
            # For Ethereum and EVM chains, convert ETH to Gwei for display
            token_symbol = get_native_token_symbol(chain)
            is_eth_based = token_symbol == "ETH"
            gas_cost_gwei = gas_cost_eth * 1e9 if is_eth_based else gas_cost_eth
            
            # Parse timestamp
            try:
                tx_date = datetime.strptime(block_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
            except ValueError:
                try:
                    tx_date = datetime.strptime(block_timestamp, "%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    tx_date = datetime.now()
            
            # Calculate USD cost using the current token price
            usd_cost = gas_cost_eth * token_price
            
            # Get token display name
            token_display = "Gwei" if is_eth_based else token_symbol
            explorer_url = get_explorer_url(chain, tx_hash)
            
            # Format transaction data
            transaction_data = {
                "chain": Config.SUPPORTED_CHAINS.get(chain, chain),
                "tx": tx_hash,
                "explorer_url": explorer_url,
                "time": tx_date.strftime("%Y-%m-%d %H:%M"),
                "gas": int(tx.get("receipt_gas_used", "0"), 16) if "receipt_gas_used" in tx else 0,
                "token_amount": round(gas_cost_gwei, 9),
                "token_symbol": token_display,
                "usd": round(usd_cost, 2)
            }
            result.append(transaction_data)
        except Exception as e:
            logger.error(f"Error processing transaction: {e}")
            continue
    
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
        
        # Validate and process address
        validated_address, error = validate_address_param(address)
        if error:
            return jsonify({"error": error}), 400
        
        address = validated_address
        
        # Get transactions from the past 3 months
        from_date = datetime.now() - timedelta(days=Config.HISTORY_DAYS)
        
        # Refresh token prices once for all chains
        token_prices = refresh_token_prices()
        
        all_transactions = []
        transactions_by_chain = {}
        
        # Fetch transactions for each supported chain
        for chain_id, chain_name in Config.SUPPORTED_CHAINS.items():
            # Skip chains that are not supported by Moralis API
            if chain_id not in Config.CHAIN_IDS:
                continue
                
            try:
                transactions = moralis.get_transactions(address, chain_id, from_date)
                if transactions:
                    processed_transactions = process_transactions(transactions, chain_id, token_prices)
                    all_transactions.extend(processed_transactions)
                    transactions_by_chain[chain_name] = processed_transactions
            except Exception as e:
                logger.error(f"Error fetching transactions for {chain_name}: {e}")
                continue
        
        # Add "All Chains" category
        transactions_by_chain["All Chains"] = all_transactions
        
        # Aggregate gas consumption by chain
        gas_blocks = aggregate_by_chain(all_transactions)
        
        # Format daily gas consumption for charting
        daily_gas = format_daily_gas(all_transactions)
        
        # Return the response, with fallbacks for empty data
        response = {
            "dailyGas": daily_gas if daily_gas else [],
            "gasBlocks": gas_blocks if gas_blocks else [],
            "transactions": transactions_by_chain if transactions_by_chain else {"All Chains": []}
        }
        
        return jsonify(response)
    except Exception as e:
        logger.error(f"Unexpected error in API: {e}")
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
    # Check if API key is set
    if not Config.MORALIS_API_KEY:
        logger.error("MORALIS_API_KEY environment variable is not set")
        print("Error: MORALIS_API_KEY environment variable is not set")
        exit(1)
        
    app.run(debug=True) 