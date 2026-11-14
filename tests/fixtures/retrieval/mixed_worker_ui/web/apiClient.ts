export interface ApiError {
  status: number;
  message: string;
}

export async function decodeApiError(response: Response): Promise<ApiError> {
  let message = response.statusText || "Request failed";
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === "string") {
      message = body.detail;
    }
  } catch {
    // The response may not contain JSON.
  }
  return { status: response.status, message };
}
