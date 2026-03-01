#!/bin/bash
# Ruta absoluta para que cron no se pierda

# Usamos --env-file para no quemar las llaves en el script
sudo docker run --rm --env-file .env motor-trading