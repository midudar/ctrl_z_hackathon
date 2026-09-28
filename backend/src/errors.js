// Ошибка API с HTTP-кодом, машинным кодом и понятным человеку текстом
export class HttpError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}
