import React from 'react'
import ReactDOM from 'react-dom/client'
import { ThemeProvider, createTheme, CssBaseline } from '@mui/material'
import { ToastContainer } from 'react-toastify'
import 'react-toastify/dist/ReactToastify.css'
import App from './App'

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary:    { main: '#3b82f6' },
    success:    { main: '#22c55e' },
    error:      { main: '#ef4444' },
    warning:    { main: '#f59e0b' },
    background: { default: '#0f172a', paper: '#1e293b' },
    divider:    '#334155',
  },
  shape: { borderRadius: 10 },
  typography: { fontFamily: "'Segoe UI', system-ui, sans-serif" },
  components: {
    MuiCard: {
      styleOverrides: { root: { border: '1px solid #334155' } },
    },
    MuiButton: {
      defaultProps: { variant: 'contained', size: 'small' },
    },
    MuiChip: {
      defaultProps: { size: 'small' },
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <App />
      <ToastContainer
        position="bottom-right"
        autoClose={3000}
        hideProgressBar={false}
        closeOnClick
        pauseOnHover
        theme="dark"
      />
    </ThemeProvider>
  </React.StrictMode>
)
