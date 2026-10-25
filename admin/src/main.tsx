import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'
import { applyEmbedTheme, installEmbedBridge } from './lib/embed'

applyEmbedTheme()
installEmbedBridge() // 부모의 hello 를 React 가 뜨기 전부터 듣는다(#180)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter basename="/admin-ui">
      <App />
    </BrowserRouter>
  </React.StrictMode>
)
