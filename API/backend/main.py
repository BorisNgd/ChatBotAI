from pymongo import MongoClient
from pydantic import BaseModel, validator,Field
import os
import logging
import httpx
import json
import motor.motor_asyncio
from dotenv import load_dotenv
from datetime import datetime
from fastapi import FastAPI, HTTPException, Body, Depends,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError
from uuid import uuid4



# Charger le fichier .env
load_dotenv()

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
        }
    )

@app.on_event("startup")
async def startup():
    # Créer l'index unique une seule fois au démarrage de l'application
    #await feedbacks.create_index([("unique_id", 1)], unique=True)
    logger.info("✅ Index unique créé pour le champ unique_id sur la collection feedbacks.")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],  # Angular tourne sur ce port
    allow_credentials=True,
    allow_methods=["*"],  # Autoriser toutes les méthodes (GET, POST, etc.)
    allow_headers=["*"],  # Autoriser tous les headers
)

# Configuration du logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

#Connexion MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://adminUser:adminPassword@mongodb:27017/?directConnection=true&authSource=chatbotai")
DATABASE_NAME = os.getenv("MONGO_INIT_DATABASE" , "chatbotai")
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client[DATABASE_NAME]
conversations = db["conversations"]
feedbacks = db["feedbacks"]

# Chargement des variables d'environnement
OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

# Modèle Pydantic pour le payload direct
class ChatRequest(BaseModel):
    user_id: str
    message: str

class Feedback(BaseModel):
    user_id: str
    response_id: str
    feedback : str
    unique_id: str = Field(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        if self.unique_id is None:
            self.unique_id = f"{self.user_id}_{self.response_id}"

    @validator('unique_id' ,pre=True , always=True)
    def set_unique_id(cls, v, values):
        return v or f"{values['user_id']}_{values['response_id']}"
    
    

@app.post("/chat")
async def chat(chat_request: ChatRequest = Body(...)):

    user_id = chat_request.user_id
    # Extraction directe du message
    message = chat_request.message

    try:
        logger.info(f"🔹 Message reçu : {message}")
        logger.info(f"OLLAMA_API_BASE_URL : {OLLAMA_API_BASE}")

        conversation = await conversations.find_one({"user_id" : user_id})
        if not conversation:
            conversation = {"user_id": user_id, "messages": [], "created_at": datetime.utcnow(), "updated_at": datetime.utcnow()}
            await conversations.insert_one(conversation)

         # Ajouter le message utilisateur
        conversation["messages"].append({"role": "user", "text": message, "timestamp": datetime.utcnow()})
        context = "\n".join([f"{msg['role']}: {msg['text']}" for msg in conversation["messages"]])

        logger.info(f"✅ Formatage de donnée : {context}")

        async with httpx.AsyncClient(timeout=20) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_API_BASE}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": context},
            ) as response:
                response.raise_for_status()

                full_response = ""
                async for line in response.aiter_lines():
                    if line.strip():
                        try:
                            json_data = json.loads(line)
                            full_response += json_data.get("response", "")
                        except json.JSONDecodeError as e:
                            logger.warning(f"⚠️ Erreur JSON : {e}")
          
                bot_response = full_response or "Je n'ai pas compris."
                response_id= str(uuid4())

            # Ajouter la réponse du bot
            conversation["messages"].append({
                "role": "bot", 
                "text": bot_response, 
                "response_id" : response_id,
                "timestamp": datetime.utcnow()})
            
            conversation["updated_at"] = datetime.utcnow()
        
        # Mettre à jour la conversation dans la base de données
        await conversations.update_one({"user_id": user_id}, {"$set": conversation})

        logger.info(f"✅ Réponse d'Ollamaaa : {response}")
        return {"response": bot_response , "response_id":response_id}

    except httpx.HTTPError as http_err:
        logger.error(f"❌ Erreur HTTP lors de l'appel à Ollama : {http_err}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erreur HTTP : {str(http_err)}")
    except Exception as e:
        logger.error(f"❌ Erreur Ollama : {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/feedback")
async def give_feedback(feedback: Feedback  = Body(...)):

    try:
        logger.info(f"🔹 Feedback reçu : {feedback.feedback} pour response_id {feedback.response_id}")

        if not feedback.unique_id:
            raise HTTPException(status_code=400 , detail="unique_id ne peut etre null")
        
        feedback_data = {
            "user_id": feedback.user_id,
            "response_id": feedback.response_id,
            "feedback": feedback.feedback,
            "timestamp": datetime.utcnow(),
            "unique_id":feedback.unique_id
        }

        existing_feedback = await feedbacks.find_one({"unique_id":feedback.unique_id})
        if existing_feedback:
            result = await feedbacks.update_one(
                {"unique_id":feedback.unique_id},
                {"$set": {"feedback" : feedback.feedback ,"timestamp": datetime.utcnow()}}
                )
            if result.modified_count == 1:
                return {"message":"Feedback mis à jour avec succès"}
            else:
                raise HTTPException(status_code = 400 ,detail = "Echec de la mise à jour du feedback")
        else:

            result = await feedbacks.insert_one(feedback_data)
            return {"message" :"Nouveau feedback avec success","feedback_id":str(result.inserted_id)}
    except DuplicateKeyError:
        raise HTTPException(status_code = 489 , detail ="Un feedback pour cette réponse existe déjà")

    except Exception as e:
        logger.error(f"❌ Erreur lors de l'enregistrement du feedback : {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))