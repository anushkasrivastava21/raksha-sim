def extract_symptoms(audio_file_path: str) -> list:
    if not audio_file_path:
        return []
    try:
        from transformers import pipeline

        print(f"\n[Audio] Processing file: {audio_file_path}")
        transcriber = pipeline("automatic-speech-recognition", model="openai/whisper-tiny", device="cpu")
        symptom_ner = pipeline("token-classification", model="d4data/biomedical-ner-all", device="cpu")
        
        # HuggingFace pipeline automatically uses ffmpeg to decode mp3, wav, flac, etc.
        transcript_result = transcriber(audio_file_path)
        transcript = transcript_result["text"].lower()
        
        print(f"[Audio] Whisper Transcript: '{transcript}'")
        
        entities = symptom_ner(transcript)
        symptoms = [ent['word'] for ent in entities if 'Sign_symptom' in ent['entity']]
        
        fallback_keywords = [
            "chest pain", "shortness of breath", "fever", "dizz"
            "y", "dizziness", "fainting", "faint", 
            "blood", "bleeding", "vomiting", "nausea", "headache", "stomach ache", "diarrhea", 
            "cough", "weakness", "fatigue", "chills", "sweating", "blurred vision", "palpitations", 
            "wheezing", "choking", "numbness", "tingling", "swelling", "rash", "itching", 
            "confusion", "seizure", "unconscious", "coma", "abdominal pain", "back pain", 
            "joint pain", "muscle ache", "sore throat", "difficulty swallowing", "loss of taste", 
            "loss of smell", "runny nose", "congestion", "bleeding gums", "toothache", "earache", 
            "hearing loss", "ringing in ears", "jaw pain", "neck pain", "stiffness", "paralysis", 
            "irregular heartbeat", "fast heart rate", "slow heart rate", "coughing up blood", 
            "vomiting blood", "bloody stool", "black stool", "painful urination", "frequent urination", 
            "inability to urinate", "pelvic pain", "heavy bleeding", "irregular periods", 
            "hot flashes", "cold flashes", "weight loss", "weight gain", "increased thirst", 
            "increased hunger", "dry mouth", "dry skin", "pale skin", "blue skin", "yellowish skin", 
            "jaundice", "dark urine", "chest tightness", "chest pressure", "sharp pain", "dull ache", 
            "throbbing pain", "radiating pain", "burning sensation", "cold sweat", "clammy skin", 
            "night sweats", "breathlessness", "dyspnea", "apnea", "hyperventilation", 
            "hypoventilation", "syncope", "presyncope", "vertigo", "lightheadedness", "disorientation", 
            "memory loss", "speech difficulty", "slurred speech", "aphasia", "dysphagia", 
            "heartburn", "acid reflux", "indigestion", "bloating", "gas", "flatulence", 
            "constipation", "tenesmus", "melena", "hematochezia", "hematuria", "polyuria", 
            "oliguria", "anuria", "dysuria", "nocturia", "incontinence", "retention", 
            "muscle weakness", "myalgia", "arthralgia", "tremor", "spasm", "twitching", "rigidity", 
            "cramping", "cramp", "charley horse", "claudication", "cyanosis", "pallor", "erythema", 
            "petechiae", "ecchymosis", "purpura", "bruising", "hives", "wheal", "macule", "papule", 
            "vesicle", "bulla", "pustule", "ulcer", "fissure", "erosion", "scaling", "crusting", 
            "alopecia", "edema", "ascites", "anasarca", "clubbing", "koilonychia", "halitosis", 
            "polydipsia", "polyphagia", "anorexia", "cachexia", "lethargy", "malaise", "asthenia", 
            "somnolence", "insomnia", "hypersomnia", "narcolepsy", "snoring", "restless legs", 
            "nightmares", "lockjaw", "trismus", "drooling", "xerostomia", "dry eyes", "photophobia",
            
            "dard", "bukhar", "khansi", "sardi", "zukam", "chakkar", "ulti", "qabz", "dast", 
            "pet dard", "sir dard", "seene mein dard", "saans lene mein takleef", "ghabrahat", 
            "pasina", "behosh", "khoon", "kamzori", "thakan", "sujan", "jalan", "chot", 
            "chakkar aana", "ulti aana", "jee machalna", "matli", "saans phulna", "sookhi khansi", 
            "balgam", "thand lagna", "kampa kampi", "kaph", "gala kharab", "gale mein dard", 
            "muh sookhna", "pyas lagna", "bhukh na lagna", "vajan ghatna", "vajan badhna", 
            "nind na aana", "neend kam aana", "khujli", "daane", "chaale", "chhala", "laal nishan", 
            "jhadna", "jodon mein dard", "kamar dard", "ghutno mein dard", "pindliyon mein dard", 
            "maspeshiyon mein dard", "nas chadna", "jhanjhanahat", "sunn padna", "lakwa", 
            "muh tedha hona", "awaaz ladkhadana", "bolne me takleef", "samajhne me takleef", 
            "bhulne ki bimari", "aankho me dard", "dhundhla dikhna", "kam dikhna", "behrapan", 
            "kaan me dard", "kaan se pani aana", "daant dard", "masudo se khoon", "muh se khoon", 
            "naak se khoon", "nakseer", "peshab me jalan", "peshab me khoon", "bar bar peshab aana", 
            "ruk ruk kar peshab aana", "dast lagna", "pechis", "khuni dast", "bawasir", "mal me khoon", 
            "pet phulna", "gas banna", "khatti dakar", "seene me jalan", "chaati me bojh", 
            "tez dhadkan", "dil ki dhadkan rukna", "saans atakna", "badan dard", "bukhaar", 
            "sardi jukham", "gala dard", "saans lene me pareshani", "chot lagna", "khoon behna", 
            "matli hona", "chati me dard", "sir ghumna", "thakawat", "kamsori", "pasina aana", 
            "saans me dikkat", "dil ki bimari", "pet me dard", "sir bhari hona", "ankhon me dard", 
            "nas dukhna", "kamar dukhna", "paer dard", "haath dard", "gardan dard", "moch", 
            "sujan aana", "laal hona", "daane nikalna", "khujli hona", "chale padna", "baal jhadna", 
            "kaan behna", "daant dukhna", "galon me dard", "gardan me dard", "gardan akadna", 
            "lakwa marna", "bolne me lafz tutna", "yaadast kamjor", "khana na pachna", "pet fholna", 
            "kabj", "bawasir hona", "raat me pasina", "bukhar lagna", "kappkappi", "aawaz baithna", 
            "kaan bajna", "chati bhari", "khatti dakar aana", "jalan hona", "kamjori", "ultiyan", 
            "thakaan", "seena dard", "khujali", "behosi", "sas phulna", "gala baithna", "jodo me dard"
        ]
        
        fallback = [kw for kw in fallback_keywords if kw in transcript]
        final_list = list(set(symptoms + fallback))
        
        print(f"[Audio] Extracted Symptoms: {final_list}\n")
        return final_list
        
    except Exception as e:
        print(f"\n[ERROR] Symptom extraction failed: {e}\n")
        return []