/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Every colour is a CSS variable so the light/dark swap happens in one
        // place and charts (which need raw values) read the same tokens.
        canvas: 'rgb(var(--canvas) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        raised: 'rgb(var(--raised) / <alpha-value>)',
        sunken: 'rgb(var(--sunken) / <alpha-value>)',
        line: 'rgb(var(--line) / <alpha-value>)',
        'line-strong': 'rgb(var(--line-strong) / <alpha-value>)',
        ink: 'rgb(var(--ink) / <alpha-value>)',
        'ink-2': 'rgb(var(--ink-2) / <alpha-value>)',
        'ink-3': 'rgb(var(--ink-3) / <alpha-value>)',
        accent: 'rgb(var(--accent) / <alpha-value>)',
        'accent-soft': 'rgb(var(--accent-soft) / <alpha-value>)',
        good: 'rgb(var(--good) / <alpha-value>)',
        warn: 'rgb(var(--warn) / <alpha-value>)',
        serious: 'rgb(var(--serious) / <alpha-value>)',
        critical: 'rgb(var(--critical) / <alpha-value>)',
        series: {
          1: 'rgb(var(--series-1) / <alpha-value>)',
          2: 'rgb(var(--series-2) / <alpha-value>)',
          3: 'rgb(var(--series-3) / <alpha-value>)',
          4: 'rgb(var(--series-4) / <alpha-value>)',
          5: 'rgb(var(--series-5) / <alpha-value>)',
          6: 'rgb(var(--series-6) / <alpha-value>)',
          7: 'rgb(var(--series-7) / <alpha-value>)',
          8: 'rgb(var(--series-8) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.02em' }],
        xs: ['0.75rem', { lineHeight: '1.125rem' }],
        sm: ['0.8125rem', { lineHeight: '1.25rem' }],
        base: ['0.875rem', { lineHeight: '1.375rem' }],
        lg: ['1rem', { lineHeight: '1.5rem' }],
        xl: ['1.25rem', { lineHeight: '1.75rem', letterSpacing: '-0.01em' }],
        '2xl': ['1.625rem', { lineHeight: '2rem', letterSpacing: '-0.02em' }],
        '3xl': ['2.125rem', { lineHeight: '2.5rem', letterSpacing: '-0.025em' }],
        '4xl': ['2.75rem', { lineHeight: '3rem', letterSpacing: '-0.03em' }],
      },
      spacing: { 4.5: '1.125rem', 13: '3.25rem', 15: '3.75rem', 18: '4.5rem', 68: '17rem' },
      borderRadius: { xs: '3px', sm: '4px', DEFAULT: '6px', md: '8px', lg: '10px' },
      transitionTimingFunction: {
        out: 'cubic-bezier(0.16, 1, 0.3, 1)',
        'in-out': 'cubic-bezier(0.65, 0, 0.35, 1)',
      },
      keyframes: {
        'fade-up': { from: { opacity: '0', transform: 'translateY(4px)' }, to: { opacity: '1', transform: 'none' } },
        'pulse-ring': {
          '0%': { boxShadow: '0 0 0 0 rgb(var(--critical) / 0.45)' },
          '70%': { boxShadow: '0 0 0 6px rgb(var(--critical) / 0)' },
          '100%': { boxShadow: '0 0 0 0 rgb(var(--critical) / 0)' },
        },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: {
        'fade-up': 'fade-up 240ms cubic-bezier(0.16, 1, 0.3, 1) both',
        'pulse-ring': 'pulse-ring 2s ease-out infinite',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
}
