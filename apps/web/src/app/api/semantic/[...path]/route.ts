import { handleSemantic } from "@/server/http/semantic-handler";
export const runtime = "nodejs";
async function handle(request: Request, ctx: RouteContext<"/api/semantic/[...path]">) {
  return handleSemantic(request, (await ctx.params).path);
}
export { handle as GET, handle as POST };
