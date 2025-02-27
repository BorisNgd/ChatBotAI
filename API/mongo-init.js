db = db.getSiblingDB('chatbotai');

db.createUser({
  user: "adminUser",
  pwd: "adminPassword",
  roles: [
    {
      role: "readWrite",
      db: "chatbotai"
    },
    {
      role: "dbAdmin",
      db: "chatbotai"
    }
  ]
});
