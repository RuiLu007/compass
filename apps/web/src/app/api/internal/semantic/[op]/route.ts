import { internalSemantic } from "@/server/http/semantic-handler";
export const runtime = "nodejs";
export async function POST(request: Request, ctx: RouteContext<"/api/internal/semantic/[op]">) {
  return internalSemantic(request, (await ctx.params).op);
}
