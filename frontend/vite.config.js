import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "./src"),
        },
    },
    server: {
        port: 5173,
        proxy: {
            "/plaid": "http://localhost:8000",
            "/splitwise": "http://localhost:8000",
            "/transactions": "http://localhost:8000",
        },
    },
});
