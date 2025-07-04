import os
import datetime
import firebase_admin
from firebase_admin import credentials, auth
from fastapi import FastAPI, Depends, HTTPException, status, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, func, select
from sqlalchemy.orm import sessionmaker, Session, declarative_base, relationship, joinedload
from pydantic import BaseModel, Field
from typing import List, Optional, Annotated

# ==================================================
# 1. 初期設定 & データベース接続
# ==================================================

# --- FastAPIアプリのインスタンス化 ---
app = FastAPI(
    title="MDS Hub API",
    description="武蔵野大学DS学部生のための匿名掲示板API",
    version="1.0.0",
)

# --- CORSミドルウェアの設定 ---
# フロントエンド(Live Server)からのアクセスを許可します
origins = [
    "http://127.0.0.1:5500",
    "http://localhost:5500",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Firebase Admin SDKの初期化 ---
try:
    # serviceAccountKey.json はこの main.py と同じ階層に配置してください
    cred_path = os.path.join(os.path.dirname(__file__), "serviceAccountKey.json")
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
except FileNotFoundError:
    print("エラー: serviceAccountKey.json が見つかりません。")
    print("Firebaseコンソールからダウンロードし、main.pyと同じディレクトリに配置してください。")
    exit()

# --- データベース設定 ---
# あやかさんが設定した情報に合わせてください
SQLALCHEMY_DATABASE_URL = "postgresql://postgres:name458@localhost:5434/yurienamekawa"
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ==================================================
# 2. SQLAlchemy データベースモデル定義
# ==================================================

class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True, index=True)
    firebase_uid = Column(String(255), nullable=False, unique=True, index=True)
    anonymous_id = Column(String(30), nullable=False, unique=True, index=True)
    term = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.now())
    posts = relationship("Post", back_populates="user")

class Topic(Base):
    __tablename__ = "topics"
    topic_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, unique=True, index=True)
    posts = relationship("Post", back_populates="topic")

class Post(Base):
    __tablename__ = "posts"
    post_id = Column(Integer, primary_key=True, index=True)
    contents = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.now())
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    topic_id = Column(Integer, ForeignKey("topics.topic_id"), nullable=False, index=True)
    user = relationship("User", back_populates="posts")
    topic = relationship("Topic", back_populates="posts")

# アプリケーション起動時にテーブルが存在しなければ作成
Base.metadata.create_all(bind=engine)

# ==================================================
# 3. Pydantic データモデル定義 (APIの入出力)
# ==================================================

class UserResponse(BaseModel):
    user_id: int
    anonymous_id: str
    term: int
    class Config:
        from_attributes = True

class TopicResponse(BaseModel):
    topic_id: int
    name: str
    class Config:
        from_attributes = True

class PostUserResponse(BaseModel):
    user_id: int
    anonymous_id: str
    term: int
    class Config:
        from_attributes = True

class PostResponse(BaseModel):
    post_id: int
    contents: str
    created_at: datetime.datetime
    user: PostUserResponse
    topic: TopicResponse
    class Config:
        from_attributes = True

# ==================================================
# 4. 依存性注入 (共通処理)
# ==================================================

# DBセッションを取得
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Firebaseトークンを検証
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
async def get_current_firebase_user(token: Annotated[str, Depends(oauth2_scheme)]):
    try:
        id_token = token.split("Bearer ")[1]
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        print(f"トークン検証エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="無効な認証情報です。",
            headers={"WWW-Authenticate": "Bearer"},
        )

# ==================================================
# 5. APIエンドポイント実装
# ==================================================

@app.get("/")
def read_root():
    return {"message": "MDS Hub APIへようこそ！"}

# --- ユーザー関連 ---

@app.post("/api/register", status_code=status.HTTP_201_CREATED)
def register_user(
    term: int = Body(..., ge=1, le=7),
    anonymous_id: str = Body(...),
    firebase_uid: str = Body(...), # フロントエンドからfirebase_uidを受け取る
    db: Session = Depends(get_db),
    # 認証は不要（このエンドポイント自体が認証の一部のため）
):
    db_user = db.query(User).filter(User.firebase_uid == firebase_uid).first()
    if db_user:
        raise HTTPException(status_code=400, detail="このユーザーは既に登録済みです。")

    new_user = User(firebase_uid=firebase_uid, anonymous_id=anonymous_id, term=term)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "ユーザー登録が成功しました。"}

@app.get("/api/users/me")
def get_user_info(db: Session = Depends(get_db), firebase_user: dict = Depends(get_current_firebase_user)):
    firebase_uid = firebase_user.get("uid")
    user = db.query(User).filter(User.firebase_uid == firebase_uid).first()
    if user:
        return {
            "is_registered": True,
            "user_id": user.user_id,
            "anonymous_id": user.anonymous_id,
            "term": user.term
        }
    return {"is_registered": False}

@app.put("/api/users/me")
def update_user_info(
    term: int = Body(..., ge=1, le=7),
    db: Session = Depends(get_db),
    firebase_user: dict = Depends(get_current_firebase_user)
):
    firebase_uid = firebase_user.get("uid")
    db_user = db.query(User).filter(User.firebase_uid == firebase_uid).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    
    db_user.term = term
    db.commit()
    return {"message": "プロフィールを更新しました。"}

# --- トピック関連 ---

@app.get("/api/topics", response_model=List[TopicResponse])
def get_topics(db: Session = Depends(get_db), firebase_user: dict = Depends(get_current_firebase_user)):
    return db.query(Topic).order_by(Topic.topic_id).all()

# --- 投稿関連 ---

@app.get("/api/posts", response_model=List[PostResponse])
def get_posts(
    topic_id: int, 
    db: Session = Depends(get_db), 
    firebase_user: dict = Depends(get_current_firebase_user)
):
    posts = db.query(Post).options(
        joinedload(Post.user), 
        joinedload(Post.topic)
    ).filter(Post.topic_id == topic_id).order_by(Post.created_at.desc()).all()
    return posts

@app.post("/api/posts", status_code=status.HTTP_201_CREATED)
def create_post(
    contents: str = Body(...),
    topic_id: int = Body(...),
    user_id: int = Body(...),
    db: Session = Depends(get_db),
    firebase_user: dict = Depends(get_current_firebase_user)
):
    # 認証されたユーザーとリクエストのユーザーIDが一致するか確認（念のため）
    user = db.query(User).filter(User.firebase_uid == firebase_user.get("uid")).first()
    if not user or user.user_id != user_id:
        raise HTTPException(status_code=403, detail="権限がありません。")
    
    new_post = Post(contents=contents, user_id=user.user_id, topic_id=topic_id)
    db.add(new_post)
    db.commit()
    return {"message": "投稿が成功しました。"}

