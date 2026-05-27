import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "./src"),
            "$sandbox": path.resolve(__dirname, "../sandbox/frontend"),
            "react": path.resolve(__dirname, "./node_modules/react"),
            "react/jsx-runtime": path.resolve(__dirname, "./node_modules/react/jsx-runtime.js"),
            "lucide-react": path.resolve(__dirname, "./node_modules/lucide-react/dist/esm/lucide-react.js"),
        },
    },
    server: {
        port: 5173,
        proxy: {
            "/api": "http://localhost:8000",
            "/plaid": "http://localhost:8000",
            "/splitwise": "http://localhost:8000",
            "/transactions": "http://localhost:8000",
        },
    },
});
