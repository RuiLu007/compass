import { internalDocuments } from "@/server/http/documents-handler";
export const runtime = "nodejs";
export async function POST(request: Request, ctx: { params: Promise<{ op: string }> }) {
  return internalDocuments(request, (await ctx.params).op);
}
