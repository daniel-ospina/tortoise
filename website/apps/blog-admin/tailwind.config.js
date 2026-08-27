/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Tortoise site tokens (website/index.html + apps/dashboard/src/index.css):
      // slate/cyan dark theme — NOT ElDato's purple.
      colors: {
        background: '#060b14',
        foreground: '#cbd5e1',
        card: { DEFAULT: '#0d1a2d', foreground: '#cbd5e1' },
        popover: { DEFAULT: '#0b1220', foreground: '#cbd5e1' },
        primary: { DEFAULT: '#06b6d4', foreground: '#04121a' },
        secondary: { DEFAULT: '#12223d', foreground: '#cbd5e1' },
        muted: { DEFAULT: '#0f1c31', foreground: '#64748b' },
        accent: { DEFAULT: '#0891b2', foreground: '#04121a' },
        destructive: { DEFAULT: '#f87171', foreground: '#04121a' },
        success: '#4ade80',
        warning: '#facc15',
        border: '#1e293b',
        input: '#1e293b',
        ring: '#06b6d4',
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontFamily: {
        // Google-Docs-style headings (owner-validated editor feel)
        serif: ['Georgia', 'Cambria', 'Times New Roman', 'serif'],
        mono: ["'SF Mono'", "'Cascadia Code'", "'Fira Code'", "'JetBrains Mono'", 'monospace'],
        sans: ["-apple-system", "BlinkMacSystemFont", "'Segoe UI'", "Roboto", "sans-serif"],
      },
      keyframes: {
        'accordion-down': { from: { height: '0' }, to: { height: 'var(--radix-accordion-content-height)' } },
        'accordion-up': { from: { height: 'var(--radix-accordion-content-height)' }, to: { height: '0' } },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
};
