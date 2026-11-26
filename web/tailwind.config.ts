import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        obsidian: "#0a0c10",
        panel: "#11141b",
        panelBorder: "#1e232e",
        bull: "#16c784",
        bear: "#ea3943",
        accent: "#3b82f6",
      },
    },
  },
  plugins: [],
};
export default config;
