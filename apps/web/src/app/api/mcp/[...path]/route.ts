import { handleMcpRequest } from "@/server/http/mcp-handler";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const GET = handleMcpRequest;
export const POST = handleMcpRequest;
export const PATCH = handleMcpRequest;
