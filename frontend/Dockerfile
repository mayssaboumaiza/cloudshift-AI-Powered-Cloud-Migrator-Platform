# frontend/Dockerfile
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 3000

RUN mkdir -p /tmp/vite-cache

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]