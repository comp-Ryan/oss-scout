import { Route, Routes } from 'react-router-dom'
import { StatusBar } from './StatusBar'
import { Desk } from './Desk'
import { Board } from '@/features/board/Board'
import { Agents } from '@/features/agents/Agents'
import { CommandPalette } from '@/features/command/CommandPalette'

export default function App() {
  return (
    <div className="min-h-screen">
      <StatusBar />
      <CommandPalette />
      <Routes>
        <Route path="/" element={<Desk />} />
        <Route path="/board" element={<Board />} />
        <Route path="/agents" element={<div className="h-[calc(100vh-49px)]"><Agents /></div>} />
      </Routes>
    </div>
  )
}
