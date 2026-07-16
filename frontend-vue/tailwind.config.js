/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Space Grotesk"', '"SF Pro Display"', 'Inter', 'Arial', 'Helvetica', 'sans-serif'],
      },
      colors: {
        bg: {
          main: '#0D0D0D',
          surface: '#171717',
          card: '#1D1A1A',
          'card-2': '#241D1F',
          hover: '#2A2023',
        },
        accent: {
          DEFAULT: '#D6A38F',
          hover: '#F0BEAA',
          dark: '#8B1538',
        },
        text: {
          main: '#F5F5F5',
          secondary: '#C8B9B3',
          muted: '#A8A8A8',
        },
        border: {
          DEFAULT: '#2A2A2A',
          divider: 'rgba(214, 163, 143, 0.16)',
        },
        status: {
          success: '#4caf50',
          danger: '#e05a4f',
          info: '#b48652',
          warning: '#c9972e',
        }
      },
      backgroundImage: {
        'gold-gradient': 'linear-gradient(135deg, #8B1538 0%, #A91D47 58%, #D6A38F 100%)',
        'panel-gradient': 'linear-gradient(180deg, #171717 0%, #1D1A1A 100%)',
        'panel-soft': 'linear-gradient(180deg, #171717 0%, #241D1F 100%)',
      },
      boxShadow: {
        'prime': '0 10px 30px rgba(0, 0, 0, 0.35)',
      },
      borderRadius: {
        'xl': '14px',
        'lg': '10px',
      }
    },
  },
  plugins: [],
}
