/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#08090c', 900: '#0d0f14', 850: '#12151c',
          800: '#171b24', 750: '#1d222d', 700: '#252b38',
          600: '#333a4a', 500: '#4a5265', 400: '#6b7488',
          300: '#9aa2b4', 200: '#c5cbd6', 100: '#e6e9ef',
        },
        sev: {
          critical: '#f0526a', high: '#f08b3c',
          medium: '#e3bd3a', low: '#4a9ee8', info: '#7c8496',
        },
        accent: { DEFAULT: '#5b8def', dim: '#3d63a8', glow: '#8fb2ff' },
        good: '#3fb984',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      keyframes: {
        'fade-up': { '0%': { opacity: '0', transform: 'translateY(6px)' }, '100%': { opacity: '1', transform: 'none' } },
        'pulse-ring': { '0%': { transform: 'scale(.8)', opacity: '.7' }, '100%': { transform: 'scale(1.8)', opacity: '0' } },
      },
      animation: {
        'fade-up': 'fade-up .28s ease-out both',
        'pulse-ring': 'pulse-ring 1.6s ease-out infinite',
      },
    },
  },
  plugins: [],
}
