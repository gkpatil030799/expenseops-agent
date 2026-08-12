export default {
    darkMode: ["class"],
    content: ["./index.html", "./src/**/*.{ts,tsx}", "../sandbox/frontend/**/*.{ts,tsx}"],
    theme: {
        extend: {
            colors: {
                canvas: "var(--ui-canvas)",
                ink: "var(--ui-ink)",
                "ui-text": "var(--ui-text)",
                "ui-muted": "var(--ui-muted)",
                "ui-border": "var(--ui-border)",
                "ui-primary": "var(--ui-primary)",
                "ui-primary-hover": "var(--ui-primary-hover)",
                "ui-primary-tint": "var(--ui-primary-tint)",
                "ui-success": "var(--ui-success)",
                "ui-warning": "var(--ui-warning)",
                "ui-error": "var(--ui-error)",
            },
            borderRadius: {
                card: "var(--ui-radius-card)",
                control: "var(--ui-radius-control)",
                lg: "var(--ui-radius-control)",
                md: "calc(var(--ui-radius-control) - 2px)",
                sm: "calc(var(--ui-radius-control) - 4px)",
            },
            boxShadow: {
                card: "var(--ui-shadow-card)",
                primary: "var(--ui-shadow-primary)",
            },
            maxWidth: {
                app: "85rem",
            },
            fontSize: {
                display: ["2rem", { lineHeight: "2.375rem", fontWeight: "600" }],
                "display-mobile": ["1.625rem", { lineHeight: "2rem", fontWeight: "600" }],
                section: ["1.25rem", { lineHeight: "1.75rem", fontWeight: "600" }],
                body: ["0.875rem", { lineHeight: "1.3125rem" }],
                caption: ["0.75rem", { lineHeight: "1.0625rem" }],
            },
            transitionDuration: {
                hover: "var(--ui-duration-hover)",
                disclosure: "var(--ui-duration-disclosure)",
                overlay: "var(--ui-duration-overlay)",
            },
        },
    },
    plugins: [],
};
