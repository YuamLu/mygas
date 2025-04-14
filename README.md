# MyGas - Blockchain Gas Consumption Tracker

A simple web application that helps users track and analyze their gas consumption across multiple blockchain networks.

![Gas Tracker Screenshot](image/README/1744372306568.png)

## 🚀 Features

- **Multi-Chain Support**: Track gas costs across Ethereum, Arbitrum, Base, Optimism, BSC, Polygon, zkSync, and Linea
- **Real Cost Analysis**: View costs in both native tokens (ETH, MATIC, etc.) and USD
- **Time-Based Analysis**: Historical data visualization for the past 90 days
- **ENS Resolution**: Support for Ethereum Name Service domains

## 🛠️ Tech Stack

- **Backend**: Python + Flask for API endpoints
- **Data Sources**: 
  - Etherscan API (Ethereum, Arbitrum, BSC, Polygon, zkSync, Linea)
  - Moralis API (Base, Optimism)
- **Frontend**: HTML, CSS, JavaScript with Chart.js

## 🏗️ Project Structure

```
mygas/
├── app.py              # Main application code (API endpoints and data processing)
├── index.html          # Single-page frontend application
├── requirements.txt    # Python dependencies
└── .env                # Environment variables (not included in repo)
```

## 📋 Prerequisites

- Python 3.8+
- Etherscan API key
- Moralis API key

## 🚦 Getting Started

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/mygas.git
   cd mygas
   ```

2. Create a `.env` file with your API keys:
   ```
   ETHERSCAN_API_KEY=your_etherscan_api_key
   MORALIS_API_KEY=your_moralis_api_key
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the application:
   ```bash
   python app.py
   ```

5. Open `http://localhost:5001` in your browser

## 🔍 Usage Example

Try with these addresses:
- ENS domain: `yuanlu.eth`
- Direct address: `0x73BE3b500f781234b21A348caFAaa23dfFf3b1B5`

## 🧪 Implementation Details

### Data Processing Flow

1. User submits an address (or ENS name)
2. Application retrieves transactions from multiple chains
3. Gas costs are calculated using:
   - For Moralis data: Using the `transaction_fee` field directly
   - For Etherscan data: Calculated from `gasPrice` × `gasUsed`
4. Results are aggregated by chain and date for visualization

### Optimizations

- API response caching
- Parallel API requests
- Token price caching (refreshed hourly)

## 📝 Notes for Developers

- The app handles future timestamps in test data
- ENS resolution works only for Ethereum addresses
- Token prices are fetched from different sources based on the chain

## 📊 Future Enhancements

- Additional chain support
- Gas fee predictions
- PDF report generation
- User accounts with saved addresses

## 📄 License

MIT License 