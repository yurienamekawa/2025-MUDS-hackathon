import os
import datetime
import firebase_admin
from firebase_admin import credentials, auth
from fastapi import FastAPI, Depends, HTTPException, status, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, func
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
SQLALCHEMY_DATABASE_URL = "postgresql://igarashiayaka:ayakapurin190127!@localhost:5432/25bbs"
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
    comments = relationship("Comment", back_populates="user")
    likes = relationship("Like", back_populates="user")

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
    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")
    likes = relationship("Like", back_populates="post", cascade="all, delete-orphan")

class Comment(Base):
    __tablename__ = "comments"
    comment_id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.post_id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.now())
    post = relationship("Post", back_populates="comments")
    user = relationship("User", back_populates="comments")

class Like(Base):
    __tablename__ = "likes"
    user_id = Column(Integer, ForeignKey("users.user_id"), primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.post_id"), primary_key=True)
    user = relationship("User", back_populates="likes")
    post = relationship("Post", back_populates="likes")

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
        orm_mode = True

class TopicResponse(BaseModel):
    topic_id: int
    name: str
    class Config:
        orm_mode = True
        
class CommentUserResponse(BaseModel):
    anonymous_id: str
    term: int
    class Config:
        orm_mode = True

class CommentResponse(BaseModel):
    comment_id: int
    content: str
    created_at: datetime.datetime
    user: CommentUserResponse
    class Config:
        orm_mode = True

class LikeResponse(BaseModel):
    user_id: int
    class Config:
        orm_mode = True

class PostResponse(BaseModel):
    post_id: int
    contents: str
    created_at: datetime.datetime
    user: UserResponse
    topic: TopicResponse
    comments: List[CommentResponse] = []
    likes: List[LikeResponse] = []
    class Config:
        orm_mode = True

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
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="無効な認証情報です。",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        id_token = token
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        print(f"トークン検証エラー: {e}")
        raise credentials_exception

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
    firebase_uid: str = Body(...),
    db: Session = Depends(get_db),
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
    user = db.query(User).filter(User.firebase_uid == firebase_user.get("uid")).first()
    if user:
        return {"is_registered": True, "user_id": user.user_id, "anonymous_id": user.anonymous_id, "term": user.term}
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
        joinedload(Post.topic),
        joinedload(Post.comments).joinedload(Comment.user),
        joinedload(Post.likes)
    ).filter(Post.topic_id == topic_id).order_by(Post.created_at.desc()).all()
    return posts

@app.post("/api/posts", status_code=status.HTTP_201_CREATED, response_model=PostResponse)
def create_post(
    contents: str = Body(...),
    topic_id: int = Body(...),
    db: Session = Depends(get_db),
    firebase_user: dict = Depends(get_current_firebase_user)
):
    user = db.query(User).filter(User.firebase_uid == firebase_user.get("uid")).first()
    if not user:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    
    new_post = Post(contents=contents, user_id=user.user_id, topic_id=topic_id)
    db.add(new_post)
    db.commit()
    db.refresh(new_post)
    return db.query(Post).options(joinedload(Post.user), joinedload(Post.topic)).filter(Post.post_id == new_post.post_id).one()

@app.delete("/api/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(
    post_id: int,
    db: Session = Depends(get_db),
    firebase_user: dict = Depends(get_current_firebase_user)
):
    user = db.query(User).filter(User.firebase_uid == firebase_user.get("uid")).first()
    if not user:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    
    post = db.query(Post).filter(Post.post_id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="投稿が見つかりません。")
    
    if post.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="この投稿を削除する権限がありません。")
    
    db.delete(post)
    db.commit()

# --- コメント関連 ---

@app.post("/api/posts/{post_id}/comments", status_code=status.HTTP_201_CREATED, response_model=CommentResponse)
def create_comment(
    post_id: int,
    content: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    firebase_user: dict = Depends(get_current_firebase_user)
):
    user = db.query(User).filter(User.firebase_uid == firebase_user.get("uid")).first()
    if not user:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    
    post = db.query(Post).filter(Post.post_id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="投稿が見つかりません。")
    
    new_comment = Comment(content=content, user_id=user.user_id, post_id=post_id)
    db.add(new_comment)
    db.commit()
    db.refresh(new_comment)
    return db.query(Comment).options(joinedload(Comment.user)).filter(Comment.comment_id == new_comment.comment_id).one()

# --- いいね関連 ---

@app.post("/api/posts/{post_id}/likes", status_code=status.HTTP_201_CREATED)
def toggle_like(
    post_id: int,
    db: Session = Depends(get_db),
    firebase_user: dict = Depends(get_current_firebase_user)
):
    user = db.query(User).filter(User.firebase_uid == firebase_user.get("uid")).first()
    if not user:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    
    post = db.query(Post).filter(Post.post_id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="投稿が見つかりません。")
    
    existing_like = db.query(Like).filter(Like.user_id == user.user_id, Like.post_id == post_id).first()
    
    if existing_like:
        db.delete(existing_like)
        db.commit()
        return {"message": "いいねを取り消しました。", "liked": False}
    else:
        new_like = Like(user_id=user.user_id, post_id=post_id)
        db.add(new_like)
        db.commit()
        return {"message": "いいねしました。", "liked": True}