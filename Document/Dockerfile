FROM texlive/texlive:latest

RUN apt-get update && apt-get install -y \
    fonts-thai-tlwg \
    && rm -rf /var/lib/apt/lists/*

# Install THSarabunNew font from local files
COPY THSarabunNew.ttf THSarabunNew_Bold.ttf THSarabunNew_Italic.ttf THSarabunNew_BoldItalic.ttf /usr/share/fonts/truetype/thsarabun/
RUN fc-cache -fv

WORKDIR /workspace

CMD ["bash"]
