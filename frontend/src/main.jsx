import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'

import App from './App'
import NewReview from './pages/NewReview'
import ReviewPage from './pages/ReviewPage'
import History from './pages/History'
import SharedReport from './pages/SharedReport'
import './styles/index.css'

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <NewReview /> },
      { path: 'reviews', element: <History /> },
      { path: 'reviews/:id', element: <ReviewPage /> },
    ],
  },
  { path: '/shared/:token', element: <SharedReport /> },
])

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)
