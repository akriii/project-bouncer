import jwt
from fastapi import Request
from fastapi.responses import RedirectResponse

# ==========================================
# 1. THE CONFIGURATION (Client changes these)
# ==========================================
# The API key you generated for this specific client
SHARED_SECRET = "sk_live_123456789" 

# The URL where your Project Bouncer is hosted
BOUNCER_URL = "https://queue.projectbouncer.com" 

# The URL on THEIR website where Bouncer should return the user
CLIENT_RETURN_URL = "https://www.client-store.com/checkout" 


@app.get("/checkout")
async def process_checkout(request: Request, bouncer_ticket: str = None):
    # (Client's existing code to get the logged-in user)
    current_user_id = request.session.get("user_id")

    # ==========================================
    # 2. THE BOUNCER INTEGRATION 
    # ==========================================
    if current_server_traffic > 1000:
        
        # A. If the user doesn't have a ticket, send them to Bouncer
        if not bouncer_ticket:
            handoff_token = jwt.encode({"user_id": current_user_id}, SHARED_SECRET)
            
            # ---> THIS IS WHERE THE REDIRECT HAPPENS <---
            redirect_url = f"{BOUNCER_URL}/?token={handoff_token}&return_to={CLIENT_RETURN_URL}"
            return RedirectResponse(url=redirect_url)

        # B. If the user returned from Bouncer, verify the ticket is authentic
        try:
            jwt.decode(bouncer_ticket, SHARED_SECRET, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            return "Invalid queue ticket. Please get back in line."

    # ==========================================
    # 3. NORMAL BUSINESS LOGIC
    # ==========================================
    # If traffic is low, or if the Bouncer ticket is valid, proceed normally.
    return process_payment_and_write_to_database(current_user_id)