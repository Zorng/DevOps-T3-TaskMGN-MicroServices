FROM eclipse-temurin:25-jdk AS build
WORKDIR /build
COPY .mvn/ .mvn/
COPY mvnw pom.xml ./
COPY src/ src/
# CI runs all suites first; Docker packaging has no Docker-in-Docker dependency.
RUN ./mvnw --batch-mode --no-transfer-progress -DskipTests package

FROM eclipse-temurin:25-jre
WORKDIR /app
RUN groupadd --system taskapp && useradd --system --gid taskapp taskapp
COPY --from=build --chown=taskapp:taskapp /build/target/task-service.jar /app/task-service.jar
USER taskapp
EXPOSE 8001
ENTRYPOINT ["java", "-jar", "/app/task-service.jar"]
