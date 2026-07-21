import { NextResponse } from "next/server"

export async function GET() {
  // You can add more sophisticated health checks here
  // For example, checking database connectivity

  return NextResponse.json({
    status: "ok",
    timestamp: new Date().toISOString(),
    uptime: process.uptime(),
  })
}
