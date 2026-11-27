import { defineConfig } from "hardhat/config";
import hardhatToolboxMochaEthers from "@nomicfoundation/hardhat-toolbox-mocha-ethers";

// Glassnet only supports PUSH0 and older opcodes, so compile for Shanghai.
const solc = { version: "0.8.24", settings: { evmVersion: "shanghai" as const } };

export default defineConfig({
  plugins: [hardhatToolboxMochaEthers],
  solidity: {
    profiles: {
      default: solc,
      production: {
        ...solc,
        settings: { ...solc.settings, optimizer: { enabled: true, runs: 200 } },
      },
    },
  },
  networks: {
    // No `accounts`: Hardhat never signs transactions here. Deploy and publish are done
    // from Python. This entry exists only so `hardhat verify` can reach the explorer.
    glassnet: {
      type: "http",
      chainType: "generic",
      url: "https://rpc.glassnet.prismo.network",
    },
  },
  chainDescriptors: {
    101001000: {
      name: "Prismo Glassnet",
      blockExplorers: {
        blockscout: {
          name: "Glassnet Explorer",
          url: "https://explorer.glassnet.prismo.network",
          apiUrl: "https://explorer.glassnet.prismo.network/api",
        },
      },
    },
  },
});
